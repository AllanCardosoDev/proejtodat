import os
import re
import json
import time
import base64
import requests
import streamlit as st
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURAÇÕES
# ============================================================

SISGAT_URL_BASE    = "https://sisgat.cbm.am.gov.br"
BRADESCO_URL_BASE  = "https://www.ne2.bradesconetempresa.b.br"
BRADESCO_LOGIN_URL = f"{BRADESCO_URL_BASE}/ibpjlogin/login.jsf"

HEADERS_BRADESCO = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}


# ============================================================
# FUNÇÕES — SISGAT
# ============================================================

@st.cache_data(ttl=3600, hash_funcs={requests.Session: lambda _: None})
def login_sisgat(url_base, username, password):
    session   = requests.Session()
    login_url = f"{url_base}/users/login"
    try:
        resp  = session.get(login_url)
        soup  = BeautifulSoup(resp.text, 'html.parser')
        csrf  = soup.find('input', {'name': '_csrfToken'})
        data  = {'username': username, 'password': password}
        if csrf:
            data['_csrfToken'] = csrf['value']
        resp2 = session.post(login_url, data=data, allow_redirects=True)
        if "login" not in resp2.url.lower() and "Acesso não autorizado" not in resp2.text:
            return session, "Login bem-sucedido!"
        return None, "Credenciais inválidas."
    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=600, hash_funcs={requests.Session: lambda _: None})
def obter_boletos_solicitados(session, url_base=SISGAT_URL_BASE):
    try:
        resp = session.get(f"{url_base}/boletos-solicitados")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        th   = soup.find('th', string='Nº do Processo')
        if not th:
            return []
        table = th.find_parent('table')
        if not table:
            return []

        headers = [
            re.sub(r'[^a-zA-Z0-9_]', '', h.lower().replace(' ', '_').replace('º', ''))
            for h in [t.get_text(strip=True) for t in table.find('thead').find_all('th')]
        ]

        boletos = []
        for row in table.find('tbody').find_all('tr'):
            cols = row.find_all('td')
            if len(cols) >= len(headers) - 1:
                item = {headers[i]: cols[i].get_text(strip=True) for i in range(min(len(headers), len(cols)))}
                link = cols[-1].find('a', string='Ver')
                if link and 'href' in link.attrs:
                    m = re.search(r'/view/(\d+)', link['href'])
                    if m:
                        item['id_boleto'] = m.group(1)
                boletos.append(item)
        return boletos
    except Exception:
        return []


@st.cache_data(ttl=600, hash_funcs={requests.Session: lambda _: None})
def obter_detalhes_boleto_sisgat(session, boleto_id, url_base=SISGAT_URL_BASE):
    try:
        resp = session.get(f"{url_base}/boletos-solicitados/view/{boleto_id}")
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        def get(label):
            th = soup.find('th', string=lambda t: t and label in t)
            if th:
                td = th.find_next_sibling('td')
                if td:
                    return td.get_text(strip=True)
            return None

        det = {}
        h3  = soup.find('h3', string=lambda t: t and 'Visualização' in (t or ''))
        if h3:
            span = h3.find('span', style=lambda s: s and 'color:red' in (s or ''))
            if span:
                det['numero_processo'] = span.get_text(strip=True)

        campos = [
            ('usuario_solicitante',       'Usuário Solicitante'),
            ('servidor_dat_gerou_boleto', 'Servidor DAT que gerou o boleto'),
            ('razao_social',              'Razão Social / Nome Fantasia do Cliente'),
            ('cpf_cnpj',                  'CPF/CNPJ'),
            ('telefone',                  'Telefone'),
            ('email',                     'Email'),
            ('tipo_taxa_solicitada',      'Tipo de Taxa Solicitada'),
            ('protecao_requerida',        'Proteção Requerida'),
            ('cep',                       'CEP'),
            ('endereco',                  'Endereço'),
            ('meu_numero',                'Meu Número'),
            ('mensagem',                  'Mensagem'),
            ('area_edificada',            'Area Edificada'),
            ('valor',                     'Valor'),
            ('status',                    'Status'),
            ('data_solicitacao_boleto',   'Data da Solicitação do Boleto'),
            ('data_ultima_edicao_boleto', 'Data da Última Edição do Boleto'),
        ]
        for chave, label in campos:
            if chave not in det or not det[chave]:
                det[chave] = get(label)

        if det.get('valor'):
            det['valor'] = det['valor'].replace('R$', '').replace('.', '').replace(',', '.').strip()
        if det.get('cpf_cnpj'):
            det['cpf_cnpj'] = re.sub(r'\D', '', det['cpf_cnpj'])

        det['nome_pagador'] = det.get('razao_social')
        det['mensagem_boleto_para_banco'] = (
            f"Processo: {det.get('numero_processo','')}, Tipo de Taxa: {det.get('tipo_taxa_solicitada','')}"
            if det.get('numero_processo') and det.get('tipo_taxa_solicitada')
            else det.get('mensagem', '')
        )

        return {k: v for k, v in det.items() if v and str(v).strip()} or None
    except Exception as e:
        st.error(f"Erro ao carregar detalhes: {e}")
        return None


# ============================================================
# FUNÇÕES — BRADESCO (requests puras)
# ============================================================

def extrair_hidden(soup):
    campos = {}
    for inp in soup.find_all('input', {'type': 'hidden'}):
        nome = inp.get('name') or inp.get('id')
        if nome:
            campos[nome] = inp.get('value', '')
    return campos


def extrair_action(soup, fallback):
    form = soup.find('form')
    if form and form.get('action'):
        a = form['action']
        return f"{BRADESCO_URL_BASE}{a}" if a.startswith('/') else a
    return fallback


def identificar_login_senha(soup):
    u, p = None, None
    for inp in soup.find_all('input'):
        name = (inp.get('name') or '').lower()
        id_  = (inp.get('id')   or '').lower()
        tipo = (inp.get('type') or 'text').lower()
        if tipo in ('hidden', 'submit', 'button', 'checkbox', 'radio'):
            continue
        if any(x in name or x in id_ for x in ['user', 'login', 'cpf', 'usuario', 'agencia', 'conta']):
            u = inp.get('name') or inp.get('id')
        if any(x in name or x in id_ for x in ['senha', 'pass', 'pwd', 'password']):
            p = inp.get('name') or inp.get('id')
    return u, p


def tentar_preencher(soup, payload, patterns, valor):
    for inp in soup.find_all(['input', 'textarea']):
        name = (inp.get('name') or '').lower()
        id_  = (inp.get('id')   or '').lower()
        for pat in patterns:
            if pat in name or pat in id_:
                campo = inp.get('name') or inp.get('id')
                if campo:
                    payload[campo] = valor
                    return campo
    return None


def bradesco_inspecionar(login, senha):
    session  = requests.Session()
    resultado = {}
    try:
        r1   = session.get(BRADESCO_LOGIN_URL, headers=HEADERS_BRADESCO, timeout=30)
        soup = BeautifulSoup(r1.text, 'html.parser')

        resultado['p1_status']    = r1.status_code
        resultado['p1_url']       = r1.url
        resultado['p1_cookies']   = {c.name: c.value for c in session.cookies}
        resultado['p1_html']      = r1.text
        resultado['p1_forms']     = [{'id': f.get('id'), 'action': f.get('action'), 'method': f.get('method')} for f in soup.find_all('form')]
        resultado['p1_inputs']    = [{'type': i.get('type','text'), 'name': i.get('name'), 'id': i.get('id'), 'value': str(i.get('value',''))[:80]} for i in soup.find_all('input')]
        resultado['p1_hidden']    = extrair_hidden(soup)

        hidden  = extrair_hidden(soup)
        action  = extrair_action(soup, BRADESCO_LOGIN_URL)
        c_u, c_p = identificar_login_senha(soup)
        payload  = {**hidden, (c_u or 'j_username'): login, (c_p or 'j_password'): senha}

        btn = soup.find('input', {'type': 'submit'}) or soup.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Entrar')

        resultado['p2_action']        = action
        resultado['p2_campo_usuario'] = c_u
        resultado['p2_campo_senha']   = c_p
        resultado['p2_payload']       = payload

        r2   = session.post(action, data=payload, headers={**HEADERS_BRADESCO, 'Referer': BRADESCO_LOGIN_URL}, allow_redirects=True, timeout=30)
        soup2 = BeautifulSoup(r2.text, 'html.parser')

        resultado['p2_status']  = r2.status_code
        resultado['p2_url']     = r2.url
        resultado['p2_cookies'] = {c.name: c.value for c in session.cookies}
        resultado['p2_html']    = r2.text
        resultado['p2_headers'] = dict(r2.headers)
        resultado['p2_links']   = [{'texto': a.get_text(strip=True), 'href': a['href']} for a in soup2.find_all('a', href=True) if a.get_text(strip=True)]
        resultado['p2_inputs']  = [{'type': i.get('type','text'), 'name': i.get('name'), 'id': i.get('id')} for i in soup2.find_all('input')]
        resultado['p2_forms']   = [{'id': f.get('id'), 'action': f.get('action')} for f in soup2.find_all('form')]

    except Exception as e:
        resultado['erro'] = str(e)
    return resultado


def bradesco_login_session(login, senha):
    session = requests.Session()
    try:
        r1       = session.get(BRADESCO_LOGIN_URL, headers=HEADERS_BRADESCO, timeout=30)
        soup     = BeautifulSoup(r1.text, 'html.parser')
        hidden   = extrair_hidden(soup)
        action   = extrair_action(soup, BRADESCO_LOGIN_URL)
        c_u, c_p = identificar_login_senha(soup)
        payload  = {**hidden, (c_u or 'j_username'): login, (c_p or 'j_password'): senha}

        btn = soup.find('input', {'type': 'submit'}) or soup.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Entrar')

        r2 = session.post(action, data=payload, headers={**HEADERS_BRADESCO, 'Referer': BRADESCO_LOGIN_URL}, allow_redirects=True, timeout=30)

        if "login" in r2.url.lower():
            return None, "Login falhou. Verifique as credenciais."

        soup2 = BeautifulSoup(r2.text, 'html.parser')
        links = [{'texto': a.get_text(strip=True), 'href': a['href']} for a in soup2.find_all('a', href=True) if a.get_text(strip=True)]
        return session, f"Login OK. URL pós-login: {r2.url}"
    except Exception as e:
        return None, str(e)


def bradesco_emitir_boleto(session, dados, log_fn=None):
    def log(m):
        if log_fn: log_fn(m)

    try:
        # Navega para cobrança
        url_cob  = f"{BRADESCO_URL_BASE}/ibpjcobranca/cobranca.jsf"
        r_cob    = session.get(url_cob, headers=HEADERS_BRADESCO, timeout=30)
        soup_cob = BeautifulSoup(r_cob.text, 'html.parser')
        log(f"📂 Cobrança: {r_cob.status_code} — {r_cob.url}")

        link_em = (
            soup_cob.find('a', string=re.compile(r'[Ee]mitir|[Bb]oleto|[Tt]ítulo'))
            or soup_cob.find('a', href=re.compile(r'emiss|boleto|titulo', re.I))
        )
        url_em = (
            (f"{BRADESCO_URL_BASE}{link_em['href']}" if link_em['href'].startswith('/') else link_em['href'])
            if link_em and link_em.get('href')
            else f"{BRADESCO_URL_BASE}/ibpjcobranca/emissaoBoleto.jsf"
        )

        r_em    = session.get(url_em, headers={**HEADERS_BRADESCO, 'Referer': url_cob}, timeout=30)
        soup_em = BeautifulSoup(r_em.text, 'html.parser')
        log(f"📄 Emissão: {r_em.status_code} — {r_em.url}")

        hidden     = extrair_hidden(soup_em)
        action_url = extrair_action(soup_em, url_em)
        payload    = {**hidden}

        campos_map = {
            'meu_numero':                (['nossonumero', 'nrotitulo', 'meunumero'],       dados.get('meu_numero','')),
            'valor':                     (['valor', 'vlrtitulo', 'vlnominal'],             dados.get('valor','')),
            'vencimento':                (['vencimento', 'dtvencimento', 'datavenc'],      dados.get('vencimento','')),
            'nome_pagador':              (['nomepagador', 'nomepag'],                      dados.get('nome_pagador','')),
            'cpf_cnpj':                  (['cpfcnpj', 'cnpj', 'cpf', 'docpagador'],       dados.get('cpf_cnpj','')),
            'endereco':                  (['endereco', 'logradouro', 'endpagador'],        dados.get('endereco','')),
            'cep':                       (['cep', 'ceppagador'],                           dados.get('cep','')),
            'mensagem_boleto_para_banco':(['instrucao', 'mensagem', 'obs'],                dados.get('mensagem_boleto_para_banco','')),
        }

        campos_encontrados = {}
        for chave, (patterns, valor) in campos_map.items():
            if valor:
                c = tentar_preencher(soup_em, payload, patterns, valor)
                campos_encontrados[chave] = c or '⚠️ não encontrado'
                log(f"  {'✅' if c else '⚠️'} {chave}: campo='{c}' valor='{str(valor)[:40]}'")

        btn = soup_em.find('input', {'type': 'submit'}) or soup_em.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Confirmar')

        log(f"📤 Submetendo para: {action_url}")
        r_final = session.post(action_url, data=payload, headers={**HEADERS_BRADESCO, 'Referer': url_em}, allow_redirects=True, timeout=60)
        log(f"📥 Resposta: {r_final.status_code} — Content-Type: {r_final.headers.get('Content-Type','')}")

        ct = r_final.headers.get('Content-Type', '')

        if 'application/pdf' in ct:
            log("✅ PDF recebido diretamente.")
            return True, "Boleto gerado!", r_final.content, campos_encontrados

        soup_final = BeautifulSoup(r_final.text, 'html.parser')
        link_pdf   = soup_final.find('a', href=re.compile(r'\.pdf', re.I)) or soup_final.find('iframe', src=re.compile(r'\.pdf|boleto', re.I))
        if link_pdf:
            href = link_pdf.get('href') or link_pdf.get('src')
            url_pdf = href if href.startswith('http') else f"{BRADESCO_URL_BASE}{href}"
            log(f"🔗 Link PDF encontrado: {url_pdf}")
            r_pdf = session.get(url_pdf, headers=HEADERS_BRADESCO, timeout=60)
            if r_pdf.content:
                return True, "Boleto PDF baixado!", r_pdf.content, campos_encontrados

        return False, "Formulário submetido mas PDF não retornado.", None, {
            'campos_encontrados': campos_encontrados,
            'url_resposta':       r_final.url,
            'html_trecho':        r_final.text[:3000],
        }

    except Exception as e:
        return False, str(e), None, {}


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(layout="wide", page_title="SISGAT + Bradesco")
st.title("🔥 SISGAT — Gestão de Boletos CBM/AM")

# ── BARRA LATERAL ────────────────────────────────────────────
with st.sidebar:
    st.header("🔐 SISGAT")
    username = st.text_input("Usuário", value="ALLAN_ATD")
    password = st.text_input("Senha", type="password", value="123456")

    if st.button("🚀 Login e Carregar Processos"):
        login_sisgat.clear()
        obter_boletos_solicitados.clear()
        obter_detalhes_boleto_sisgat.clear()

        with st.spinner("Autenticando..."):
            st.session_state['sisgat_session'], st.session_state['login_status'] = login_sisgat(
                SISGAT_URL_BASE, username, password
            )

        if st.session_state['sisgat_session']:
            with st.spinner("Carregando processos..."):
                st.session_state['processos_listados'] = obter_boletos_solicitados(
                    st.session_state['sisgat_session']
                )
            st.success(f"✅ {len(st.session_state['processos_listados'])} processo(s).")
        else:
            st.error(st.session_state['login_status'])

    st.markdown("---")
    st.header("🏦 Bradesco")
    bradesco_login = st.text_input("Login", value="mpps00033")
    bradesco_senha = st.text_input("Senha Bradesco", type="password", value="832cbmam")
    st.session_state['bradesco_login'] = bradesco_login
    st.session_state['bradesco_senha'] = bradesco_senha

    st.markdown("---")

    if st.session_state.get('processos_listados'):
        st.subheader("📋 Processo")
        opcoes = ["Selecione..."] + [
            f"{p.get('n_do_processo','N/A')} - {p.get('cliente','N/A')} - {p.get('tipo_de_taxa','N/A')}"
            for p in st.session_state['processos_listados']
        ]
        sel = st.selectbox("Processos", opcoes, key="sel_processo")
        processo_atual = None
        if sel != "Selecione...":
            m = re.match(r'(\d+)', sel)
            if m:
                num = m.group(1)
                for p in st.session_state['processos_listados']:
                    if p.get('n_do_processo') == num:
                        processo_atual = p
                        break
        st.session_state['selected_process'] = processo_atual


# ── ABAS ─────────────────────────────────────────────────────
aba1, aba2 = st.tabs(["📋 Gestão de Boletos", "🔍 Inspecionar Bradesco"])


with aba1:
    processo = st.session_state.get('selected_process')

    if not processo:
        st.info("👈 Faça login e selecione um processo na barra lateral.")
    else:
        processo_id = processo.get('id_boleto')

        c1, c2, c3 = st.columns(3)
        c1.metric("Nº do Processo", processo.get('n_do_processo', 'N/A'))
        c2.metric("Cliente",        processo.get('cliente', 'N/A'))
        c3.metric("Tipo de Taxa",   processo.get('tipo_de_taxa', 'N/A'))
        st.markdown("---")

        with st.spinner("Carregando detalhes..."):
            detalhes = obter_detalhes_boleto_sisgat(
                st.session_state['sisgat_session'], processo_id
            )

        if not detalhes:
            st.error("Não foi possível carregar os detalhes.")
        else:
            st.subheader("🏦 Gerar Boleto no Bradesco")

            col1, col2 = st.columns(2)
            with col1:
                data_venc = st.date_input("📅 Vencimento")
            with col2:
                st.text_input("💰 Valor (R$)", value=detalhes.get('valor',''), disabled=True)

            with st.expander("📋 Dados do boleto", expanded=False):
                d1, d2 = st.columns(2)
                with d1:
                    st.write(f"**Pagador:** {detalhes.get('nome_pagador','N/A')}")
                    st.write(f"**CPF/CNPJ:** {detalhes.get('cpf_cnpj','N/A')}")
                    st.write(f"**Endereço:** {detalhes.get('endereco','N/A')}")
                    st.write(f"**CEP:** {detalhes.get('cep','N/A')}")
                with d2:
                    st.write(f"**Processo:** {detalhes.get('numero_processo','N/A')}")
                    st.write(f"**Tipo de Taxa:** {detalhes.get('tipo_taxa_solicitada','N/A')}")
                    st.write(f"**Meu Número:** {detalhes.get('meu_numero','N/A')}")
                    st.write(f"**Mensagem:** {detalhes.get('mensagem_boleto_para_banco','N/A')}")

            # Autenticar no Bradesco
            if st.button("🔑 Autenticar no Bradesco", use_container_width=True):
                with st.spinner("Autenticando..."):
                    sess, msg = bradesco_login_session(
                        st.session_state.get('bradesco_login',''),
                        st.session_state.get('bradesco_senha',''),
                    )
                if sess:
                    st.session_state['bradesco_session'] = sess
                    st.success(f"✅ {msg}")
                else:
                    st.error(msg)

            # Emitir boleto
            if st.button("🏦 Emitir Boleto", type="primary", use_container_width=True):
                if not st.session_state.get('bradesco_session'):
                    st.warning("Autentique no Bradesco primeiro.")
                else:
                    logs_lista   = []
                    log_box      = st.empty()

                    def log_fn(msg):
                        logs_lista.append(msg)
                        log_box.markdown("<br>".join(logs_lista), unsafe_allow_html=True)

                    dados_boleto = {
                        **detalhes,
                        "vencimento": data_venc.strftime("%d/%m/%Y"),
                    }

                    with st.spinner("Emitindo boleto..."):
                        ok, msg, pdf_bytes, campos = bradesco_emitir_boleto(
                            st.session_state['bradesco_session'],
                            dados_boleto,
                            log_fn=log_fn,
                        )

                    st.markdown("---")
                    if ok and pdf_bytes:
                        st.success(msg)
                        st.balloons()
                        nome = f"boleto_{detalhes.get('numero_processo', processo_id)}.pdf"
                        b64  = base64.b64encode(pdf_bytes).decode()
                        st.markdown(
                            f'<a href="data:application/pdf;base64,{b64}" download="{nome}" '
                            f'style="display:inline-block;padding:10px 22px;background:#1a6e1a;'
                            f'color:white;text-decoration:none;border-radius:6px;font-weight:bold;">'
                            f'📥 Baixar Boleto PDF</a>',
                            unsafe_allow_html=True
                        )
                    else:
                        st.error(msg)
                        if isinstance(campos, dict) and campos.get('html_trecho'):
                            with st.expander("🔎 HTML de resposta"):
                                st.code(campos['html_trecho'], language='html')
                        st.write("**Campos identificados:**", campos)

            st.markdown("---")
            with st.expander("🗂️ Detalhes Completos", expanded=False):
                items  = list(detalhes.items())
                metade = len(items) // 2
                e1, e2 = st.columns(2)
                with e1:
                    for k, v in items[:metade]:
                        st.write(f"**{k.replace('_',' ').title()}:** {v}")
                with e2:
                    for k, v in items[metade:]:
                        st.write(f"**{k.replace('_',' ').title()}:** {v}")


with aba2:
    st.subheader("🔍 Inspecionar Site do Bradesco")
    st.caption("Faz GET e POST via requests e exibe tudo para mapear os campos JSF.")

    i1, i2 = st.columns(2)
    with i1:
        ins_login = st.text_input("Login", value="mpps00033", key="ins_l")
    with i2:
        ins_senha = st.text_input("Senha", type="password", value="832cbmam", key="ins_s")

    if st.button("🔎 Inspecionar", type="primary", use_container_width=True):
        with st.spinner("Inspecionando..."):
            r = bradesco_inspecionar(ins_login, ins_senha)

        st.markdown("### Passo 1 — GET na página de login")
        st.write(f"**Status:** {r.get('p1_status')} | **URL:** {r.get('p1_url')}")
        st.write("**Cookies:**")
        st.json(r.get('p1_cookies', {}))
        st.write("**Formulários:**")
        st.json(r.get('p1_forms', []))
        st.write("**Inputs:**")
        st.json(r.get('p1_inputs', []))
        st.write("**Hidden fields:**")
        st.json(r.get('p1_hidden', {}))
        with st.expander("📄 HTML completo — Passo 1"):
            st.code(r.get('p1_html', ''), language='html')

        st.markdown("---")
        st.markdown("### Passo 2 — POST com credenciais")
        st.write(f"**Status:** {r.get('p2_status')} | **URL:** {r.get('p2_url')}")
        st.write(f"**Campo usuário:** `{r.get('p2_campo_usuario')}` | **Campo senha:** `{r.get('p2_campo_senha')}`")
        st.write("**Payload enviado:**")
        st.json(r.get('p2_payload', {}))
        st.write("**Links pós-login:**")
        st.json(r.get('p2_links', []))
        st.write("**Inputs pós-login:**")
        st.json(r.get('p2_inputs', []))
        st.write("**Headers:**")
        st.json(r.get('p2_headers', {}))
        with st.expander("📄 HTML completo — Passo 2"):
            st.code(r.get('p2_html', ''), language='html')

        if r.get('erro'):
            st.error(f"Erro: {r['erro']}")
