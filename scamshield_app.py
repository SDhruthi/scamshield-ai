"""ScamShield AI - paste a suspicious message (or upload a screenshot) and get
red flags, a plain-language explanation, and safe next steps, powered by Gemma.

Setup:
    pip install streamlit google-genai pillow
    set GEMINI_API_KEY=your-key          (Command Prompt)
    $env:GEMINI_API_KEY="your-key"       (PowerShell)

Run:
    streamlit run scamshield_app.py
"""

import html
import json
import os
import re
import time

import streamlit as st
from google import genai
from PIL import Image

DEFAULT_MODEL = "gemma-4-26b-a4b-it"  # editable in the sidebar
FALLBACK_MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]  # tried automatically if the first one fails

PROMPT = """You are ScamShield AI, a friendly assistant that helps ordinary people
spot scams, phishing and fake offers in messages (SMS, email, WhatsApp, social media).

Analyze the message below{image_note}. Look for red flags such as:
- Urgency or threats (account blocked, legal action, "act now")
- Requests for OTPs, PINs, passwords, card or bank details
- Payment requests via gift cards, crypto, or unusual UPI/wire transfers
- Prizes, lotteries, jobs or investments that sound too good to be true
- Spoofed sender names, shortened or lookalike links
- Impersonation of banks, courier firms, government bodies or relatives

Respond with ONLY a JSON object, no extra text, in exactly this format:
{{
  "verdict": "scam" | "suspicious" | "likely_safe",
  "confidence": <number between 0 and 1>,
  "red_flags": [{{"flag": "short title", "why": "plain-language explanation"}}],
  "explanation": "2-3 simple sentences explaining the overall verdict",
  "next_steps": ["safe verification or action step", "..."]
}}

Message to analyze:
\"\"\"
{message}
\"\"\"
"""

VERDICTS = {
    "scam": {"icon": "🚨", "label": "Likely a scam", "color": "#ef4444",
             "blurb": "Do not click, reply, or share anything."},
    "suspicious": {"icon": "⚠️", "label": "Suspicious", "color": "#f59e0b",
                   "blurb": "Be careful and verify before you act."},
    "likely_safe": {"icon": "✅", "label": "Looks likely safe", "color": "#22c55e",
                    "blurb": "No major warning signs found."},
}

EXAMPLES = {
    "🏦 Bank alert": "URGENT: Your bank account will be blocked today. Share the OTP sent to your phone to verify KYC now: bit.ly/xyz123",
    "🎁 Prize": "Congratulations! You won a ₹25,00,000 lottery. Pay ₹4,999 processing fee to claim your prize today.",
    "💬 Normal chat": "Hi, are we meeting today at 5pm?",
}

CSS = """
<style>
#MainMenu, footer, header [data-testid="stToolbar"] {visibility: hidden;}
.block-container {max-width: 820px; padding-top: 2.2rem; padding-bottom: 3rem;}

.hero {
    text-align: center; padding: 2rem 1rem 1.6rem; margin-bottom: 1.4rem;
    border-radius: 20px;
    background: radial-gradient(120% 140% at 50% 0%, rgba(99,102,241,.28), rgba(14,165,233,.10) 55%, transparent 80%);
    border: 1px solid rgba(148,163,184,.22);
}
.hero .shield {font-size: 3rem; line-height: 1;}
.hero h1 {
    margin: .35rem 0 .2rem; font-size: 2.5rem; font-weight: 800; letter-spacing: -.02em;
    background: linear-gradient(90deg, #818cf8, #38bdf8);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}
.hero p {margin: 0 auto; max-width: 540px; opacity: .8; font-size: 1.02rem;}
.chips {margin-top: 1rem; display: flex; gap: .5rem; justify-content: center; flex-wrap: wrap;}
.chip {
    font-size: .8rem; padding: .25rem .7rem; border-radius: 999px;
    border: 1px solid rgba(148,163,184,.35); background: rgba(148,163,184,.10);
}

.section-title {font-size: 1.05rem; font-weight: 700; margin: 1.6rem 0 .6rem;}

.verdict {
    border-radius: 18px; padding: 1.3rem 1.5rem; margin-top: 1.2rem;
    border: 1px solid var(--c); border-left: 8px solid var(--c);
    background: linear-gradient(90deg, color-mix(in srgb, var(--c) 18%, transparent), transparent 75%);
}
.verdict .top {display: flex; align-items: center; gap: .8rem;}
.verdict .icon {font-size: 2.2rem; line-height: 1;}
.verdict .label {font-size: 1.6rem; font-weight: 800; color: var(--c);}
.verdict .blurb {opacity: .85; margin-top: .15rem;}
.meter-wrap {margin-top: 1rem;}
.meter-text {font-size: .8rem; opacity: .75; margin-bottom: .3rem;}
.meter {height: 9px; border-radius: 99px; background: rgba(148,163,184,.25); overflow: hidden;}
.meter > div {height: 100%; border-radius: 99px; background: var(--c);}

.explain {
    margin-top: .8rem; padding: 1rem 1.2rem; border-radius: 14px;
    background: rgba(148,163,184,.10); border: 1px solid rgba(148,163,184,.2); line-height: 1.55;
}

.flag {
    display: flex; gap: .8rem; padding: .85rem 1rem; margin-bottom: .55rem; border-radius: 14px;
    background: rgba(148,163,184,.10); border: 1px solid rgba(148,163,184,.2);
    border-left: 5px solid var(--c);
}
.flag .dot {font-size: 1.15rem; line-height: 1.5;}
.flag b {display: block; margin-bottom: .1rem;}
.flag span {opacity: .85; font-size: .93rem; line-height: 1.5;}

.step {
    display: flex; gap: .8rem; align-items: flex-start; padding: .75rem 1rem; margin-bottom: .5rem;
    border-radius: 14px; background: rgba(34,197,94,.09); border: 1px solid rgba(34,197,94,.28);
}
.step .n {
    flex: 0 0 1.7rem; height: 1.7rem; border-radius: 50%; display: grid; place-items: center;
    font-size: .85rem; font-weight: 700; background: #22c55e; color: #052e16;
}
.step div {line-height: 1.5; padding-top: .1rem;}

.disclaimer {margin-top: 1.8rem; text-align: center; font-size: .8rem; opacity: .6;}

div.stButton > button[kind="primary"] {
    width: 100%; padding: .7rem 1rem; font-size: 1.05rem; font-weight: 700; border: none;
    border-radius: 12px; background: linear-gradient(90deg, #6366f1, #0ea5e9); color: white;
}
div.stButton > button[kind="primary"]:hover {filter: brightness(1.1);}
div.stButton > button[kind="secondary"] {border-radius: 999px; font-size: .85rem;}
textarea {border-radius: 12px !important;}
</style>
"""


def analyze(api_key: str, model: str, message: str, image: Image.Image | None) -> dict:
    client = genai.Client(api_key=api_key)
    prompt = PROMPT.format(
        message=message or "(see the attached screenshot)",
        image_note=" and the attached screenshot" if image else "",
    )
    contents = [prompt, image] if image else prompt

    # Try the chosen model first, then the others if Google has a temporary server problem (500/503).
    models = [model] + [m for m in FALLBACK_MODELS if m != model]
    response = None
    last_exc = None
    for m in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=m, contents=contents)
                break
            except Exception as exc:
                last_exc = exc
                transient = any(code in str(exc) for code in ("500", "503", "UNAVAILABLE", "INTERNAL"))
                if not transient:
                    raise
                time.sleep(1.5)
        if response is not None:
            break
    if response is None:
        raise last_exc

    text = (response.text or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)  # models sometimes add ```json fences
    fallback = {"verdict": "suspicious", "confidence": 0, "red_flags": [],
                "explanation": text, "next_steps": []}
    if not match:
        return fallback
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback


def default_api_key() -> str:
    """Key from the GEMINI_API_KEY env variable, or from Streamlit secrets when hosted online."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:  # no secrets file locally - that's fine
            key = ""
    return key


def esc(value) -> str:
    return html.escape(str(value or ""))


def show_result(result: dict) -> None:
    v = VERDICTS.get(result.get("verdict"), VERDICTS["suspicious"])
    try:
        conf = max(0.0, min(1.0, float(result.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0

    st.markdown(
        f"""
        <div class="verdict" style="--c:{v['color']}">
          <div class="top">
            <div class="icon">{v['icon']}</div>
            <div><div class="label">{v['label']}</div><div class="blurb">{v['blurb']}</div></div>
          </div>
          <div class="meter-wrap">
            <div class="meter-text">Confidence: {conf:.0%}</div>
            <div class="meter"><div style="width:{conf*100:.0f}%"></div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if result.get("explanation"):
        st.markdown(f'<div class="section-title">💡 Why</div><div class="explain">{esc(result["explanation"])}</div>',
                    unsafe_allow_html=True)

    flags = result.get("red_flags", [])
    if flags:
        st.markdown('<div class="section-title">🚩 Warning signs</div>', unsafe_allow_html=True)
        for f in flags:
            title, why = (f.get("flag", ""), f.get("why", "")) if isinstance(f, dict) else (f, "")
            st.markdown(
                f'<div class="flag" style="--c:{v["color"]}"><div class="dot">⚠️</div>'
                f'<div><b>{esc(title)}</b><span>{esc(why)}</span></div></div>',
                unsafe_allow_html=True,
            )

    steps = result.get("next_steps", [])
    if steps:
        st.markdown('<div class="section-title">🛡️ What to do next</div>', unsafe_allow_html=True)
        for i, s in enumerate(steps, 1):
            st.markdown(f'<div class="step"><div class="n">{i}</div><div>{esc(s)}</div></div>',
                        unsafe_allow_html=True)

    st.markdown('<div class="disclaimer">ScamShield is an AI aid and can make mistakes. When in doubt, '
                'contact the organisation through its official website or phone number.</div>',
                unsafe_allow_html=True)


def set_example(text: str) -> None:
    st.session_state["message"] = text


def main() -> None:
    st.set_page_config(page_title="ScamShield AI", page_icon="🛡️", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)

    with st.sidebar:
        st.header("⚙️ Settings")
        model = st.text_input("Gemma model", value=DEFAULT_MODEL).strip().strip(",").strip()
        api_key = st.text_input("Gemini API key", type="password",
                                value=default_api_key()).strip()
        st.caption("Leave the key blank to use the GEMINI_API_KEY environment "
                   "variable (or the app's saved secret when hosted online).")

    st.markdown(
        """
        <div class="hero">
          <div class="shield">🛡️</div>
          <h1>ScamShield AI</h1>
          <p>Got a message that feels off? Paste it or upload a screenshot and get the warning signs
          explained in plain language, plus safe steps to take.</p>
          <div class="chips"><span class="chip">📝 Paste text</span><span class="chip">🖼️ Upload screenshot</span>
          <span class="chip">🔍 See red flags</span><span class="chip">✅ Get safe steps</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption("Try an example:")
    cols = st.columns(len(EXAMPLES))
    for col, (name, text) in zip(cols, EXAMPLES.items()):
        col.button(name, on_click=set_example, args=(text,), use_container_width=True, key=f"ex_{name}")

    message = st.text_area(
        "Message to check",
        key="message",
        height=150,
        placeholder="e.g. Your bank account will be blocked. Click this link now.",
    )
    upload = st.file_uploader("Or upload a screenshot (optional)", type=["png", "jpg", "jpeg"])
    image = Image.open(upload) if upload else None
    if image:
        st.image(image, caption="Uploaded screenshot", width=320)

    if st.button("🔍 Analyze", type="primary"):
        if not api_key:
            st.error("Add your Gemini API key in the sidebar first.")
            return
        if not message.strip() and image is None:
            st.warning("Paste a message or upload a screenshot first.")
            return

        with st.spinner("ScamShield is checking this message..."):
            try:
                result = analyze(api_key, model, message.strip(), image)
            except Exception as exc:  # show API errors (bad key, wrong model, no image support)
                st.error(f"Something went wrong: {exc}")
                if image:
                    st.info("This model may not accept images. Try text only, "
                            "or switch to a multimodal Gemma model in the sidebar.")
                return

        show_result(result)


if __name__ == "__main__":
    main()
