import os
import re
import time
import json
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

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

# Armazena sessões ativas por ID
sessoes_bradesco: dict[str, requests.Session] = {}


# ============================================================
# MODELOS
# ============================================================

class LoginSisgat(BaseModel):
    url_base: str
    username: str
    password: str


class LoginBradesco(BaseModel):
    login: str
    senha: str


class DadosBoleto(BaseModel):
    sessao_id: str
    meu_numero:               str = ""
    valor:                    str = ""
    vencimento:               str = ""
    nome_pagador:             str = ""
    cpf_cnpj:                 str = ""
    endereco:                 str = ""
    cep:                      str = ""
    mensagem_boleto_para_banco: str = ""


# ============================================================
# HELPERS
# ============================================================

def extrair_campos_hidden(soup: BeautifulSoup) -> dict:
    campos = {}
    for inp in soup.find_all('input', {'type': 'hidden'}):
        nome = inp.get('name') or inp.get('id')
        if nome:
            campos[nome] = inp.get('value', '')
    return campos


def extrair_action(soup: BeautifulSoup, fallback: str) -> str:
    form = soup.find('form')
    if form and form.get('action'):
        action = form['action']
        return f"{BRADESCO_URL_BASE}{action}" if action.startswith('/') else action
    return fallback


def identificar_campos_login(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    campo_usuario = None
    campo_senha   = None
    for inp in soup.find_all('input'):
        name = (inp.get('name') or '').lower()
        id_  = (inp.get('id')   or '').lower()
        tipo = (inp.get('type') or 'text').lower()
        if tipo in ('hidden', 'submit', 'button', 'checkbox', 'radio'):
            continue
        if any(p in name or p in id_ for p in ['user', 'login', 'cpf', 'usuario', 'agencia', 'conta']):
            campo_usuario = inp.get('name') or inp.get('id')
        if any(p in name or p in id_ for p in ['senha', 'pass', 'pwd', 'password']):
            campo_senha = inp.get('name') or inp.get('id')
    return campo_usuario, campo_senha


def tentar_preencher(soup: BeautifulSoup, payload: dict, patterns: list[str], valor: str):
    """Tenta encontrar um campo pelo name/id usando patterns e adiciona ao payload."""
    for inp in soup.find_all('input'):
        name = (inp.get('name') or '').lower()
        id_  = (inp.get('id')   or '').lower()
        for p in patterns:
            if p in name or p in id_:
                campo = inp.get('name') or inp.get('id')
                if campo:
                    payload[campo] = valor
                    return campo
    for ta in soup.find_all('textarea'):
        name = (ta.get('name') or '').lower()
        id_  = (ta.get('id')   or '').lower()
        for p in patterns:
            if p in name or p in id_:
                campo = ta.get('name') or ta.get('id')
                if campo:
                    payload[campo] = valor
                    return campo
    return None


# ============================================================
# ENDPOINTS — SISGAT
# ============================================================

@app.post("/sisgat/login")
def sisgat_login(body: LoginSisgat):
    session  = requests.Session()
    login_url = f"{body.url_base}/users/login"
    try:
        resp = session.get(login_url)
        soup = BeautifulSoup(resp.text, 'html.parser')
        csrf = soup.find('input', {'name': '_csrfToken'})
        data = {'username': body.username, 'password': body.password}
        if csrf:
            data['_csrfToken'] = csrf['value']
        resp2 = session.post(login_url, data=data, allow_redirects=True)
        if "login" not in resp2.url.lower() and "Acesso não autorizado" not in resp2.text:
            # Serializa cookies para retornar ao frontend
            cookies = {c.name: c.value for c in session.cookies}
            return {"sucesso": True, "cookies": cookies, "url_base": body.url_base}
        return {"sucesso": False, "mensagem": "Credenciais inválidas."}
    except Exception as e:
        return {"sucesso": False, "mensagem": str(e)}


@app.get("/sisgat/boletos")
def sisgat_boletos(url_base: str, cookies: str):
    session = requests.Session()
    session.cookies.update(json.loads(cookies))
    try:
        resp = session.get(f"{url_base}/boletos-solicitados")
        soup = BeautifulSoup(resp.text, 'html.parser')
        header = soup.find('th', string='Nº do Processo')
        if not header:
            return {"sucesso": False, "boletos": []}
        table = header.find_parent('table')
        if not table:
            return {"sucesso": False, "boletos": []}

        headers_raw = [th.get_text(strip=True) for th in table.find('thead').find_all('th')]
        clean       = [re.sub(r'[^a-zA-Z0-9_]', '', h.lower().replace(' ', '_').replace('º', '')) for h in headers_raw]

        boletos = []
        for row in table.find('tbody').find_all('tr'):
            cols = row.find_all('td')
            if len(cols) >= len(clean) - 1:
                item = {}
                for i, h in enumerate(clean):
                    if i < len(cols):
                        item[h] = cols[i].get_text(strip=True)
                link = cols[-1].find('a', string='Ver')
                if link and 'href' in link.attrs:
                    m = re.search(r'/view/(\d+)', link['href'])
                    if m:
                        item['id_boleto'] = m.group(1)
                boletos.append(item)

        return {"sucesso": True, "boletos": boletos}
    except Exception as e:
        return {"sucesso": False, "mensagem": str(e), "boletos": []}


@app.get("/sisgat/boleto/{boleto_id}")
def sisgat_detalhes(boleto_id: str, url_base: str, cookies: str):
    session = requests.Session()
    session.cookies.update(json.loads(cookies))
    try:
        resp = session.get(f"{url_base}/boletos-solicitados/view/{boleto_id}")
        soup = BeautifulSoup(resp.text, 'html.parser')
        detalhes = {}

        def get(label):
            th = soup.find('th', string=lambda t: t and label in t)
            if th:
                td = th.find_next_sibling('td')
                if td:
                    return td.get_text(strip=True)
            return None

        h3 = soup.find('h3', string=lambda t: t and 'Visualização' in t)
        if h3:
            span = h3.find('span', style=lambda s: s and 'color:red' in s)
            if span:
                detalhes['numero_processo'] = span.get_text(strip=True)

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
            detalhes[chave] = get(label)

        if detalhes.get('valor'):
            detalhes['valor'] = detalhes['valor'].replace('R$','').replace('.','').replace(',','.').strip()
        if detalhes.get('cpf_cnpj'):
            detalhes['cpf_cnpj'] = re.sub(r'\D', '', detalhes['cpf_cnpj'])

        detalhes['nome_pagador'] = detalhes.get('razao_social')
        if detalhes.get('numero_processo') and detalhes.get('tipo_taxa_solicitada'):
            detalhes['mensagem_boleto_para_banco'] = (
                f"Processo: {detalhes['numero_processo']}, "
                f"Tipo de Taxa: {detalhes['tipo_taxa_solicitada']}"
            )
        else:
            detalhes['mensagem_boleto_para_banco'] = detalhes.get('mensagem', '')

        limpos = {k: v for k, v in detalhes.items() if v and str(v).strip()}
        return {"sucesso": True, "detalhes": limpos}
    except Exception as e:
        return {"sucesso": False, "mensagem": str(e)}


# ============================================================
# ENDPOINTS — BRADESCO (requests puras, sem Selenium)
# ============================================================

@app.post("/bradesco/inspecionar")
def bradesco_inspecionar(body: LoginBradesco):
    """
    Faz GET + POST no Bradesco e retorna tudo que encontrou.
    Usado para mapear os campos reais do formulário JSF.
    """
    session  = requests.Session()
    resultado = {}

    try:
        resp_get = session.get(BRADESCO_LOGIN_URL, headers=HEADERS_BRADESCO, timeout=30)
        soup     = BeautifulSoup(resp_get.text, 'html.parser')

        resultado['passo1_status']    = resp_get.status_code
        resultado['passo1_url']       = resp_get.url
        resultado['passo1_cookies']   = {c.name: c.value for c in session.cookies}
        resultado['passo1_html']      = resp_get.text
        resultado['passo1_forms']     = [
            {'id': f.get('id'), 'action': f.get('action'), 'method': f.get('method')}
            for f in soup.find_all('form')
        ]
        resultado['passo1_inputs']    = [
            {'type': i.get('type','text'), 'name': i.get('name'), 'id': i.get('id'), 'value': str(i.get('value',''))[:100]}
            for i in soup.find_all('input')
        ]
        resultado['passo1_hidden']    = extrair_campos_hidden(soup)

        hidden        = extrair_campos_hidden(soup)
        action_url    = extrair_action(soup, BRADESCO_LOGIN_URL)
        c_user, c_pwd = identificar_campos_login(soup)

        payload = {**hidden}
        payload[c_user or 'j_username'] = body.login
        payload[c_pwd  or 'j_password'] = body.senha

        btn = soup.find('input', {'type': 'submit'}) or soup.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Entrar')

        resultado['passo2_action_url']    = action_url
        resultado['passo2_campo_usuario'] = c_user
        resultado['passo2_campo_senha']   = c_pwd
        resultado['passo2_payload']       = payload

        resp_post = session.post(
            action_url, data=payload,
            headers={**HEADERS_BRADESCO, 'Referer': BRADESCO_LOGIN_URL},
            allow_redirects=True, timeout=30
        )
        soup_pos = BeautifulSoup(resp_post.text, 'html.parser')

        resultado['passo2_status']  = resp_post.status_code
        resultado['passo2_url']     = resp_post.url
        resultado['passo2_cookies'] = {c.name: c.value for c in session.cookies}
        resultado['passo2_html']    = resp_post.text
        resultado['passo2_headers'] = dict(resp_post.headers)
        resultado['passo2_links']   = [
            {'texto': a.get_text(strip=True), 'href': a['href']}
            for a in soup_pos.find_all('a', href=True) if a.get_text(strip=True)
        ]
        resultado['passo2_forms']   = [
            {'id': f.get('id'), 'action': f.get('action')}
            for f in soup_pos.find_all('form')
        ]
        resultado['passo2_inputs']  = [
            {'type': i.get('type','text'), 'name': i.get('name'), 'id': i.get('id')}
            for i in soup_pos.find_all('input')
        ]

    except Exception as e:
        resultado['erro'] = str(e)

    return resultado


@app.post("/bradesco/login")
def bradesco_login(body: LoginBradesco):
    """
    Autentica no Bradesco e retorna o ID da sessão para uso posterior.
    """
    session = requests.Session()
    try:
        resp_get = session.get(BRADESCO_LOGIN_URL, headers=HEADERS_BRADESCO, timeout=30)
        soup     = BeautifulSoup(resp_get.text, 'html.parser')
        hidden   = extrair_campos_hidden(soup)
        action   = extrair_action(soup, BRADESCO_LOGIN_URL)
        c_u, c_p = identificar_campos_login(soup)

        payload = {**hidden}
        payload[c_u or 'j_username'] = body.login
        payload[c_p or 'j_password'] = body.senha

        btn = soup.find('input', {'type': 'submit'}) or soup.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Entrar')

        resp_post = session.post(
            action, data=payload,
            headers={**HEADERS_BRADESCO, 'Referer': BRADESCO_LOGIN_URL},
            allow_redirects=True, timeout=30
        )

        if "login" in resp_post.url.lower():
            return {"sucesso": False, "mensagem": "Login falhou. Verifique as credenciais."}

        sessao_id = f"bradesco_{int(time.time())}"
        sessoes_bradesco[sessao_id] = session

        return {
            "sucesso":    True,
            "sessao_id":  sessao_id,
            "url_pos_login": resp_post.url,
            "links": [
                {'texto': a.get_text(strip=True), 'href': a['href']}
                for a in BeautifulSoup(resp_post.text, 'html.parser').find_all('a', href=True)
                if a.get_text(strip=True)
            ]
        }
    except Exception as e:
        return {"sucesso": False, "mensagem": str(e)}


@app.post("/bradesco/emitir")
def bradesco_emitir(body: DadosBoleto):
    """
    Navega até a área de emissão de boleto e submete o formulário
    com os dados recebidos.
    """
    session = sessoes_bradesco.get(body.sessao_id)
    if not session:
        return {"sucesso": False, "mensagem": "Sessão não encontrada. Faça login primeiro."}

    try:
        # Tenta URL de cobrança
        url_cob = f"{BRADESCO_URL_BASE}/ibpjcobranca/cobranca.jsf"
        resp_cob = session.get(url_cob, headers=HEADERS_BRADESCO, timeout=30)
        soup_cob = BeautifulSoup(resp_cob.text, 'html.parser')

        # Procura link de emissão
        link_emissao = (
            soup_cob.find('a', string=re.compile(r'[Ee]mitir|[Bb]oleto|[Tt]ítulo'))
            or soup_cob.find('a', href=re.compile(r'emiss|boleto|titulo', re.I))
        )

        if link_emissao and link_emissao.get('href'):
            href = link_emissao['href']
            url_emissao = href if href.startswith('http') else f"{BRADESCO_URL_BASE}{href}"
        else:
            url_emissao = f"{BRADESCO_URL_BASE}/ibpjcobranca/emissaoBoleto.jsf"

        resp_em = session.get(url_emissao, headers={**HEADERS_BRADESCO, 'Referer': url_cob}, timeout=30)
        soup_em = BeautifulSoup(resp_em.text, 'html.parser')

        hidden     = extrair_campos_hidden(soup_em)
        action_url = extrair_action(soup_em, url_emissao)
        payload    = {**hidden}

        # Preenche os campos identificando pelos patterns
        campos_preenchidos = {}

        if body.meu_numero:
            c = tentar_preencher(soup_em, payload, ['nossonumero', 'nrotitulo', 'meunumero', 'nosso_numero'], body.meu_numero)
            campos_preenchidos['meu_numero'] = c

        if body.valor:
            c = tentar_preencher(soup_em, payload, ['valor', 'vlrtitulo', 'vlnominal'], body.valor)
            campos_preenchidos['valor'] = c

        if body.vencimento:
            c = tentar_preencher(soup_em, payload, ['vencimento', 'dtvencimento', 'datavenc'], body.vencimento)
            campos_preenchidos['vencimento'] = c

        if body.nome_pagador:
            c = tentar_preencher(soup_em, payload, ['nomepagador', 'nomepag'], body.nome_pagador)
            campos_preenchidos['nome_pagador'] = c

        if body.cpf_cnpj:
            c = tentar_preencher(soup_em, payload, ['cpfcnpj', 'cnpj', 'cpf', 'docpagador'], body.cpf_cnpj)
            campos_preenchidos['cpf_cnpj'] = c

        if body.endereco:
            c = tentar_preencher(soup_em, payload, ['endereco', 'logradouro', 'endpagador'], body.endereco)
            campos_preenchidos['endereco'] = c

        if body.cep:
            c = tentar_preencher(soup_em, payload, ['cep', 'ceppagador'], body.cep)
            campos_preenchidos['cep'] = c

        if body.mensagem_boleto_para_banco:
            c = tentar_preencher(soup_em, payload, ['instrucao', 'mensagem', 'obs'], body.mensagem_boleto_para_banco)
            campos_preenchidos['mensagem'] = c

        btn = soup_em.find('input', {'type': 'submit'}) or soup_em.find('button', {'type': 'submit'})
        if btn and btn.get('name'):
            payload[btn['name']] = btn.get('value', 'Confirmar')

        resp_final = session.post(
            action_url, data=payload,
            headers={**HEADERS_BRADESCO, 'Referer': url_emissao},
            allow_redirects=True, timeout=60
        )

        content_type = resp_final.headers.get('Content-Type', '')

        # PDF retornado diretamente
        if 'application/pdf' in content_type:
            import base64
            pdf_b64 = base64.b64encode(resp_final.content).decode()
            return {"sucesso": True, "pdf_base64": pdf_b64, "campos_preenchidos": campos_preenchidos}

        # HTML com link para PDF
        soup_final = BeautifulSoup(resp_final.text, 'html.parser')
        link_pdf   = soup_final.find('a', href=re.compile(r'\.pdf', re.I))
        if not link_pdf:
            link_pdf = soup_final.find('iframe', src=re.compile(r'\.pdf|boleto', re.I))

        if link_pdf:
            href_pdf = link_pdf.get('href') or link_pdf.get('src')
            url_pdf  = href_pdf if href_pdf.startswith('http') else f"{BRADESCO_URL_BASE}{href_pdf}"
            resp_pdf = session.get(url_pdf, headers=HEADERS_BRADESCO, timeout=60)
            if resp_pdf.content:
                import base64
                pdf_b64 = base64.b64encode(resp_pdf.content).decode()
                return {"sucesso": True, "pdf_base64": pdf_b64, "campos_preenchidos": campos_preenchidos}

        return {
            "sucesso":            False,
            "mensagem":           "Formulário submetido mas PDF não retornado.",
            "url_resposta":       resp_final.url,
            "html_resposta":      resp_final.text[:3000],
            "campos_preenchidos": campos_preenchidos,
            "payload_enviado":    {k: v for k, v in payload.items() if 'token' not in k.lower()},
        }

    except Exception as e:
        return {"sucesso": False, "mensagem": str(e)}


@app.get("/health")
def health():
    return {"status": "ok"}
