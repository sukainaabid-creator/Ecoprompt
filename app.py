"""
EcoPrompt (Python + Streamlit version)
Run it with:   streamlit run app.py

Two plain-text files sit next to this one and can be edited at any time:
    filler_words.txt  -> words the tool DELETES
    phrases.txt       -> long phrase => short phrase
"""
import os
import re
import difflib
import streamlit as st

# ------------------------------------------------------------------
# SETTINGS (the numbers we assume)
# ------------------------------------------------------------------
CHARS_PER_TOKEN = 4        # tokens = characters / 4   (same as the experiment document)
WH_PER_TOKEN = 0.0006      # energy per token, in Watt-hours
ROUND_TOKENS = False       # False = exact chars/4 (matches the spreadsheet formula)
                           # True  = round to whole tokens
# UAE is listed FIRST so it is the default, as in the experiment document.
GRID_FACTORS = {
    "UAE - 556 g CO2/kWh (experiment default)": 556,
    "Global average - 435 g CO2/kWh (IEA, 2025)": 435,
}

EXAMPLES = {
    "Wordy email request": "Hi there, I hope this message finds you well. I was wondering if you could please kindly help me write an email to my landlord? I just really need it to sound professional, and it is important to note that I want you to keep it fairly short.",
    "Over-hedged instruction": "Could you please, in order to save time, take into consideration the majority of the feedback we received on a daily basis, and make a decision about the changes at this point in time?",
    "Already concise": "Summarize this article in three bullet points.",
}

# ------------------------------------------------------------------
# READING AND WRITING THE WORD LISTS
# ------------------------------------------------------------------
# JUDGE MODE: on the online copy (where a file called judge_mode.txt exists),
# words added in the sidebar last for ONE visitor's visit only, so one visitor
# can never change the lists for everyone else. On the school laptop there is
# no judge_mode.txt, so changes are saved to the text files as before.
JUDGE_MODE = os.path.exists("judge_mode.txt")


def read_lines(filename):
    """Return the non-empty lines of a text file as a list."""
    try:
        with open(filename, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        lines = []
    if JUDGE_MODE:
        lines += st.session_state.get("extra", {}).get(filename, [])
    return lines


def add_line(filename, line):
    """Add one line to the end of a text file (if it is not already there)."""
    line = line.strip()
    if line and line.lower() not in [x.lower() for x in read_lines(filename)]:
        if JUDGE_MODE:
            st.session_state.setdefault("extra", {}).setdefault(filename, []).append(line)
        else:
            with open(filename, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    return False


def read_phrases():
    """Turn the lines 'long => short' into a list of (long, short) pairs."""
    pairs = []
    for line in read_lines("phrases.txt"):
        if "=>" in line:
            long_part, short_part = line.split("=>", 1)
            pairs.append((long_part.strip(), short_part.strip()))
    return pairs


# ------------------------------------------------------------------
# THE OPTIMISER
# ------------------------------------------------------------------
def replace_ignoring_case(text, old, new):
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


def optimize_prompt(text):
    # 1. swap long phrases for short ones (longest phrases first)
    for long_phrase, short_phrase in sorted(read_phrases(), key=lambda p: -len(p[0])):
        text = replace_ignoring_case(text, long_phrase, short_phrase)

    # 2. delete filler words (whole words only, so "every" survives "very")
    for word in read_lines("filler_words.txt"):
        text = re.sub(r"\b" + re.escape(word) + r"\b,?\s*", "", text, flags=re.IGNORECASE)

    # 3. tidy up
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)        # no space before punctuation
    text = re.sub(r"([,.!?;:])(\s*[,.!?;:])+", r"\1", text)  # no doubled punctuation
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")      # one space; no leading comma
    text = re.sub(r"(^\w|[.!?]\s+\w)", lambda m: m.group(0).upper(), text)  # capitals
    return text


# ------------------------------------------------------------------
# THE MATHS
# ------------------------------------------------------------------
def count_tokens(text):
    """Characters / 4. Exact by default, so it matches the spreadsheet."""
    tokens = len(text) / CHARS_PER_TOKEN
    return round(tokens) if ROUND_TOKENS else tokens


def fmt_tokens(x):
    """Show whole numbers as 12, otherwise as 12.25."""
    return f"{x:.0f}" if float(x).is_integer() else f"{x:.2f}"


def compute_stats(original, optimized, grid_factor):
    """
    Same formula as the experiment document:
        Tokens saved = baseline tokens - optimized tokens
        Energy (Wh)  = tokens saved x 0.0006
        CO2 (g)      = (Wh / 1000) x grid factor
    Energy and CO2 are BOTH worked out from the same 'saved' number,
    so they can never disagree with each other.
    """
    empty = dict(orig_chars=0, opt_chars=0, orig_tokens=0, opt_tokens=0,
                 saved=0, percent=0, wh=0, kwh=0, co2=0, grid=grid_factor)
    if not original.strip():
        return empty
    orig_tokens = count_tokens(original)
    opt_tokens = count_tokens(optimized)
    saved = max(0, orig_tokens - opt_tokens)
    percent = saved / orig_tokens * 100 if orig_tokens else 0
    wh = saved * WH_PER_TOKEN
    kwh = wh / 1000
    co2 = kwh * grid_factor
    return dict(orig_chars=len(original), opt_chars=len(optimized),
                orig_tokens=orig_tokens, opt_tokens=opt_tokens,
                saved=saved, percent=percent, wh=wh, kwh=kwh, co2=co2,
                grid=grid_factor)


def fmt_energy(wh):
    """Pick a unit so the number is never a 'funny 0.00'."""
    if wh >= 1000:   return f"{wh/1000:,.2f} kWh"
    if wh >= 1:      return f"{wh:.2f} Wh"
    if wh >= 0.001:  return f"{wh*1000:.1f} mWh"
    return f"{wh*1e6:.1f} µWh"


def fmt_co2(g):
    if g >= 1000:    return f"{g/1000:,.2f} kg"
    if g >= 1:       return f"{g:.2f} g"
    if g >= 0.001:   return f"{g*1000:.1f} mg"
    return f"{g*1e6:.1f} µg"


# ------------------------------------------------------------------
# THE RED / GREEN COMPARISON
# ------------------------------------------------------------------
def escape(word):
    return word.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def diff_html(original, optimized):
    a, b = original.split(), optimized.split()
    matcher = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b])
    pieces = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            pieces += [escape(w) for w in a[i1:i2]]
        if tag in ("delete", "replace"):
            pieces += [f'<span class="gone">{escape(w)}</span>' for w in a[i1:i2]]
        if tag in ("insert", "replace"):
            pieces += [f'<span class="new">{escape(w)}</span>' for w in b[j1:j2]]
    return " ".join(pieces)


# ------------------------------------------------------------------
# THE SCREEN
# ------------------------------------------------------------------
st.set_page_config(page_title="EcoPrompt", page_icon="🌿", layout="wide")

st.markdown("""
<style>
  .stApp { background: #F2F4EA; }
  h1, h2, h3 { font-family: Georgia, serif; color: #1C2B21; }
  .tagline { color: #4E5B4D; font-size: 1.05rem; margin-top: -0.6rem; }
  .box { background: #fff; border: 1px solid #DCE2D2; border-radius: 10px;
         padding: 14px; line-height: 1.7; color: #4E5B4D; }
  .best { background: #E4EEE3; border: 1px solid #CFE0CC; color: #1C2B21; }
  .gone { color: #A6483F; background: #F5E4E0; text-decoration: line-through; padding: 0 2px; border-radius: 3px; }
  .new  { color: #1F4D37; background: #E4EEE3; padding: 0 2px; border-radius: 3px; }
  .label { font-family: monospace; font-size: 11px; letter-spacing: .06em;
           text-transform: uppercase; color: #7C8778; margin: 10px 0 6px; }
  div[data-testid="stMetric"] { background: #FCFDF8; border: 1px solid #DCE2D2;
         border-radius: 14px; padding: 12px 14px; }
  div[data-testid="stMetricValue"] { color: #B98B3E; font-family: Georgia, serif; font-size: clamp(1.0rem, 2.1vw, 1.7rem); }
  div[data-testid="stMetricLabel"] p { white-space: normal !important; }
  /* never cut off the unit (e.g. "20.1 mWh") with "..." */
  div[data-testid="stMetricValue"] > div { overflow: visible !important; text-overflow: clip !important; white-space: nowrap !important; }
  .stButton > button[kind="primary"] { background: #2F6D4F; border: none; }
</style>
""", unsafe_allow_html=True)

st.title("🌿 EcoPrompt")
st.markdown('<p class="tagline">Shorter prompts, lower energy, less carbon — see the '
            'environmental cost of the words you didn\'t need.</p>', unsafe_allow_html=True)

# remember the text between clicks
if "prompt" not in st.session_state:
    st.session_state.prompt = EXAMPLES["Wordy email request"]
if "log" not in st.session_state:
    st.session_state.log = []

# example buttons
cols = st.columns(3)
for col, (name, text) in zip(cols, EXAMPLES.items()):
    if col.button("Try: " + name, use_container_width=True):
        st.session_state.prompt = text

# input
st.markdown('<div class="label">Your prompt</div>', unsafe_allow_html=True)
prompt = st.text_area("Your prompt", key="prompt", height=170, label_visibility="collapsed")
st.caption(f"{len(prompt)} characters · ~{fmt_tokens(count_tokens(prompt))} tokens")

grid_name = st.selectbox("Carbon grid factor", list(GRID_FACTORS.keys()))
grid_factor = GRID_FACTORS[grid_name]

if st.button("Optimize prompt", type="primary", use_container_width=True):
    optimized_now = optimize_prompt(prompt)
    s = compute_stats(prompt, optimized_now, grid_factor)
    if prompt.strip():
        st.session_state.log.append(s)

# results (calculated on every run so numbers are always current)
optimized = optimize_prompt(prompt)
stats = compute_stats(prompt, optimized, grid_factor)

left, right = st.columns(2)
with left:
    st.markdown('<div class="label">What changed</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="box">{diff_html(prompt, optimized) or "Nothing to compare yet."}</div>',
                unsafe_allow_html=True)
with right:
    st.markdown('<div class="label">Optimized prompt</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="box best">{escape(optimized) or "—"}</div>', unsafe_allow_html=True)
    st.code(optimized or "—", language=None)   # has a built-in copy button

# Same order as the test sheet: original tokens, optimized tokens, tokens saved,
# length reduction, energy saved, CO2 avoided.
r1a, r1b, r1c = st.columns(3)
r1a.metric("Original tokens", fmt_tokens(stats["orig_tokens"]))
r1b.metric("Optimized tokens", fmt_tokens(stats["opt_tokens"]))
r1c.metric("Tokens saved", fmt_tokens(stats["saved"]))
r2a, r2b, r2c = st.columns(3)
r2a.metric("Length reduction", f"{round(stats['percent'])}%")
r2b.metric("Energy saved", fmt_energy(stats["wh"]))
r2c.metric("CO₂ avoided", fmt_co2(stats["co2"]))

# scale it up so the impact is visible
if stats["saved"] > 0:
    n = 1_000_000
    st.info(f"**At scale:** if {n:,} people made this same saving, that is "
            f"**{fmt_energy(stats['wh']*n)}** of energy and "
            f"**{fmt_co2(stats['co2']*n)}** of CO₂ avoided.")
st.caption(f"1 token = {CHARS_PER_TOKEN} characters · {WH_PER_TOKEN} Wh per token · "
           f"{grid_factor} g CO₂/kWh")

with st.expander("Show the working (check the numbers by hand)"):
    st.markdown(f"""
| Step | Calculation | Result |
|---|---|---|
| Baseline tokens | {stats['orig_chars']} chars ÷ {CHARS_PER_TOKEN} | **{fmt_tokens(stats['orig_tokens'])}** |
| Optimized tokens | {stats['opt_chars']} chars ÷ {CHARS_PER_TOKEN} | **{fmt_tokens(stats['opt_tokens'])}** |
| Tokens saved | {fmt_tokens(stats['orig_tokens'])} − {fmt_tokens(stats['opt_tokens'])} | **{fmt_tokens(stats['saved'])}** |
| Energy saved | {fmt_tokens(stats['saved'])} × {WH_PER_TOKEN} Wh | **{stats['wh']:.6f} Wh** |
| In kWh | {stats['wh']:.6f} ÷ 1000 | **{stats['kwh']:.9f} kWh** |
| CO₂ avoided | {stats['kwh']:.9f} × {grid_factor} g/kWh | **{stats['co2']:.6f} g** |
""")

# session log
st.markdown("---")
st.markdown('<div class="label">Session log — this session only</div>', unsafe_allow_html=True)
log = st.session_state.log
if not log:
    st.write("No prompts optimized yet.")
else:
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Prompts optimized", len(log))
    t2.metric("Tokens saved", fmt_tokens(sum(e["saved"] for e in log)))
    t3.metric("Energy saved", fmt_energy(sum(e["wh"] for e in log)))
    t4.metric("CO₂ avoided", fmt_co2(sum(e["co2"] for e in log)))
    csv = ("chars_before,chars_after,tokens_before,tokens_after,tokens_saved,"
           "percent,energy_wh,co2_g,grid_g_per_kwh\n" + "\n".join(
        f"{e['orig_chars']},{e['opt_chars']},{e['orig_tokens']},{e['opt_tokens']},"
        f"{e['saved']},{e['percent']:.2f},{e['wh']:.6f},{e['co2']:.6f},{e['grid']}" for e in log))
    st.download_button("Export CSV", csv, file_name="ecoprompt-session.csv")
    if st.button("Clear session"):
        st.session_state.log = []
        st.rerun()

# ------------------------------------------------------------------
# SIDEBAR: TEACH THE TOOL NEW WORDS WHILE TESTING
# ------------------------------------------------------------------
with st.sidebar:
    st.header("🛠 Teach the tool")
    if JUDGE_MODE:
        st.caption("Try adding your own words. They work immediately and last for your visit only.")
    else:
        st.caption("Changes are saved to the text files and work immediately.")

    new_filler = st.text_input("Add a filler word (gets deleted)")
    if st.button("Add filler word") and new_filler:
        if add_line("filler_words.txt", new_filler):
            st.success(f'Added "{new_filler}"')
            st.rerun()
        else:
            st.info("Already in the list.")

    st.markdown("---")
    new_long = st.text_input("Long phrase")
    new_short = st.text_input("Replace with (leave empty to delete it)")
    if st.button("Add phrase rule") and new_long:
        if add_line("phrases.txt", f"{new_long} => {new_short}"):
            st.success("Rule added")
            st.rerun()
        else:
            st.info("Already in the list.")

    with st.expander("See current lists"):
        st.write("**Filler words:**", ", ".join(read_lines("filler_words.txt")))
        st.write("**Phrase rules:**")
        for long_p, short_p in read_phrases():
            st.text(f"{long_p}  →  {short_p or '(delete)'}")
