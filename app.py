import streamlit as st
import requests
import json
import base64
import re

BACKEND_URL = "http://localhost:8000"  # Troque pelo endereço do seu backend em produção
SISGAT_URL_BASE = "https://sisgat.cbm.am.gov.br"


def api(method: str, endpoint: str, **kwargs):
    """Helper para chamar o backend."""
    try:
        resp = getattr(requests, method)(f"{BACKEND_URL}{endpoint}", timeout=60, **kwargs)
        return resp.json()
    except Exception as e:
        return {"sucesso": False, "mensagem": str(e)}


st.set_page_config(layout="wide", page_title="SISGAT + Bradesco")
st.title("🔥 SISGAT — Gestão de Boletos CBM/AM")

# ============================================================
# BARRA LATERAL
# ============================================================

with st.sidebar:
    st.header("🔐 Acesso ao SISGAT")
    username = st.text_input("Usuário SISGAT", value="ALLAN_ATD")
    password = st.text_input("Senha SISGAT", type="password", value="123456")

    if st.button("🚀 Login e Carregar Processos"):
        with st.spinner("Autenticando..."):
            r = api("post", "/sisgat/login", json={
                "url_base": SISGAT_URL_BASE,
                "username": username,
                "password": password
            })

        if r.get("sucesso"):
            st.session_state['sisgat_cookies'] = json.dumps(r["cookies"])
            st.session_state['sisgat_url_base'] = r["url_base"]

            with st.spinner("Carregando processos..."):
                r2 = api("get", "/sisgat/boletos", params={
                    "url_base": SISGAT_URL_BASE,
                    "cookies":  st.session_state['sisgat_cookies']
                })

            if r2.get("sucesso"):
                st.session_state['processos_listados'] = r2["boletos"]
                st.success(f"✅ {len(r2['boletos'])} processo(s) carregado(s).")
            else:
                st.error(r2.get("mensagem", "Erro ao carregar processos."))
        else:
            st.error(r.get("mensagem", "Falha no login."))

    st.markdown("---")
    st.header("🏦 Credenciais Bradesco")
    bradesco_login = st.text_input("Login Bradesco", value="mpps00033")
    bradesco_senha = st.text_input("Senha Bradesco", type="password", value="832cbmam")
    st.session_state['bradesco_login'] = bradesco_login
    st.session_state['bradesco_senha'] = bradesco_senha

    st.markdown("---")

    if st.session_state.get('processos_listados'):
        st.subheader("📋 Selecione um Processo")

        opcoes = ["Selecione um processo..."] + [
            f"{p.get('n_do_processo','N/A')} - {p.get('cliente','N/A')} - {p.get('tipo_de_taxa','N/A')}"
            for p in st.session_state['processos_listados']
        ]

        selecionado = st.selectbox("Processos", opcoes, key="selected_display")

        processo_atual = None
        if selecionado != "Selecione um processo...":
            m = re.match(r'(\d+)', selecionado)
            if m:
                num = m.group(1)
                for p in st.session_state['processos_listados']:
                    if p.get('n_do_processo') == num:
                        processo_atual = p
                        break

        st.session_state['selected_process'] = processo_atual


# ============================================================
# ÁREA PRINCIPAL — ABAS
# ============================================================

aba1, aba2 = st.tabs(["📋 Gestão de Boletos", "🔍 Inspecionar Bradesco"])


# ── ABA 1 — GESTÃO ──────────────────────────────────────────
with aba1:
    processo = st.session_state.get('selected_process')

    if not processo:
        st.info("👈 Faça login e selecione um processo na barra lateral.")
        st.stop()

    processo_id = processo.get('id_boleto')

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Nº do Processo", processo.get('n_do_processo', 'N/A'))
    with col2:
        st.metric("Cliente", processo.get('cliente', 'N/A'))
    with col3:
        st.metric("Tipo de Taxa", processo.get('tipo_de_taxa', 'N/A'))

    st.markdown("---")

    with st.spinner("Carregando detalhes..."):
        r_det = api("get", f"/sisgat/boleto/{processo_id}", params={
            "url_base": SISGAT_URL_BASE,
            "cookies":  st.session_state.get('sisgat_cookies', '{}')
        })

    if not r_det.get("sucesso"):
        st.error("Não foi possível carregar os detalhes.")
        st.stop()

    detalhes = r_det["detalhes"]

    st.subheader("🏦 Etapa 2 — Gerar Boleto no Bradesco")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        data_venc = st.date_input("📅 Data de Vencimento")
    with col_c2:
        st.text_input("💰 Valor (R$)", value=detalhes.get('valor', ''), disabled=True)

    with st.expander("📋 Dados do boleto", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**Pagador:** {detalhes.get('nome_pagador','N/A')}")
            st.write(f"**CPF/CNPJ:** {detalhes.get('cpf_cnpj','N/A')}")
            st.write(f"**Endereço:** {detalhes.get('endereco','N/A')}")
            st.write(f"**CEP:** {detalhes.get('cep','N/A')}")
        with c2:
            st.write(f"**Processo:** {detalhes.get('numero_processo','N/A')}")
            st.write(f"**Tipo de Taxa:** {detalhes.get('tipo_taxa_solicitada','N/A')}")
            st.write(f"**Meu Número:** {detalhes.get('meu_numero','N/A')}")
            st.write(f"**Mensagem:** {detalhes.get('mensagem_boleto_para_banco','N/A')}")

    # Login no Bradesco (backend)
    if st.button("🔑 Autenticar no Bradesco", use_container_width=True):
        with st.spinner("Autenticando no Bradesco..."):
            r_login = api("post", "/bradesco/login", json={
                "login": st.session_state.get('bradesco_login',''),
                "senha": st.session_state.get('bradesco_senha',''),
            })
        if r_login.get("sucesso"):
            st.session_state['bradesco_sessao_id'] = r_login["sessao_id"]
            st.success(f"✅ Autenticado! Sessão: `{r_login['sessao_id']}`")
        else:
            st.error(r_login.get("mensagem","Falha no login Bradesco."))

    # Emitir boleto
    if st.button("🏦 Emitir Boleto", type="primary", use_container_width=True):
        sessao_id = st.session_state.get('bradesco_sessao_id')
        if not sessao_id:
            st.warning("Autentique no Bradesco primeiro.")
        else:
            with st.spinner("Emitindo boleto no backend..."):
                r_emit = api("post", "/bradesco/emitir", json={
                    "sessao_id":                 sessao_id,
                    "meu_numero":                detalhes.get('meu_numero',''),
                    "valor":                     detalhes.get('valor',''),
                    "vencimento":                data_venc.strftime("%d/%m/%Y"),
                    "nome_pagador":              detalhes.get('nome_pagador',''),
                    "cpf_cnpj":                  detalhes.get('cpf_cnpj',''),
                    "endereco":                  detalhes.get('endereco',''),
                    "cep":                       detalhes.get('cep',''),
                    "mensagem_boleto_para_banco": detalhes.get('mensagem_boleto_para_banco',''),
                })

            if r_emit.get("sucesso") and r_emit.get("pdf_base64"):
                st.success("✅ Boleto gerado com sucesso!")
                st.balloons()
                nome_pdf = f"boleto_{detalhes.get('numero_processo', processo_id)}.pdf"
                pdf_bytes = base64.b64decode(r_emit["pdf_base64"])
                b64 = base64.b64encode(pdf_bytes).decode()
                st.markdown(
                    f'<a href="data:application/pdf;base64,{b64}" download="{nome_pdf}" '
                    f'style="display:inline-block;padding:10px 22px;background:#1a6e1a;'
                    f'color:white;text-decoration:none;border-radius:6px;font-weight:bold;">'
                    f'📥 Baixar Boleto PDF</a>',
                    unsafe_allow_html=True
                )
            else:
                st.error(r_emit.get("mensagem","Boleto não gerado."))
                if r_emit.get("html_resposta"):
                    with st.expander("🔎 HTML de resposta do Bradesco"):
                        st.code(r_emit["html_resposta"], language="html")
                if r_emit.get("campos_preenchidos"):
                    st.write("**Campos preenchidos:**", r_emit["campos_preenchidos"])

    st.markdown("---")
    with st.expander("🗂️ Detalhes Completos", expanded=False):
        items  = list(detalhes.items())
        metade = len(items) // 2
        c1, c2 = st.columns(2)
        with c1:
            for k, v in items[:metade]:
                st.write(f"**{k.replace('_',' ').title()}:** {v}")
        with c2:
            for k, v in items[metade:]:
                st.write(f"**{k.replace('_',' ').title()}:** {v}")


# ── ABA 2 — INSPEÇÃO ────────────────────────────────────────
with aba2:
    st.subheader("🔍 Inspecionar Site do Bradesco")
    st.caption("Faz GET e POST no Bradesco via backend e exibe tudo para mapear os campos JSF.")

    c1, c2 = st.columns(2)
    with c1:
        ins_login = st.text_input("Login", value="mpps00033", key="ins_l")
    with c2:
        ins_senha = st.text_input("Senha", type="password", value="832cbmam", key="ins_s")

    if st.button("🔎 Inspecionar", type="primary", use_container_width=True):
        with st.spinner("Inspecionando via backend..."):
            r = api("post", "/bradesco/inspecionar", json={
                "login": ins_login,
                "senha": ins_senha
            })

        st.markdown("### Passo 1 — GET na página de login")
        st.write(f"**Status:** {r.get('passo1_status')} | **URL:** {r.get('passo1_url')}")
        st.write("**Cookies:**")
        st.json(r.get('passo1_cookies', {}))
        st.write("**Formulários:**")
        st.json(r.get('passo1_forms', []))
        st.write("**Inputs:**")
        st.json(r.get('passo1_inputs', []))
        st.write("**Hidden fields:**")
        st.json(r.get('passo1_hidden', {}))
        with st.expander("📄 HTML — Passo 1"):
            st.code(r.get('passo1_html', ''), language='html')

        st.markdown("---")
        st.markdown("### Passo 2 — POST com credenciais")
        st.write(f"**Status:** {r.get('passo2_status')} | **URL:** {r.get('passo2_url')}")
        st.write(f"**Campo usuário:** `{r.get('passo2_campo_usuario')}` | **Campo senha:** `{r.get('passo2_campo_senha')}`")
        st.write("**Payload enviado:**")
        st.json(r.get('passo2_payload', {}))
        st.write("**Links pós-login:**")
        st.json(r.get('passo2_links', []))
        st.write("**Inputs pós-login:**")
        st.json(r.get('passo2_inputs', []))
        st.write("**Headers:**")
        st.json(r.get('passo2_headers', {}))
        with st.expander("📄 HTML — Passo 2"):
            st.code(r.get('passo2_html', ''), language='html')

        if r.get('erro'):
            st.error(f"Erro: {r['erro']}")
