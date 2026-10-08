"""MedMinder AI - Medicine Reminder Automation Using AI Agents (Streamlit UI + orchestrator)."""
from __future__ import annotations

import io
import json
from datetime import datetime, time as dtime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import config
import db
import llm
import notifier
from agents import adherence_agent, parser_agent, reminder_agent, scheduler_agent

st.set_page_config(page_title="MedMinder AI", page_icon="💊", layout="wide")
db.init_db()
st_autorefresh(interval=config.REFRESH_MS, key="tick")  # re-run every few seconds to check for due doses


# ---------------------------------------------------------------- helpers & notifications
def trigger_desktop_notification(title: str, body: str, play_sound: bool = True):
    """Fires a native browser/OS desktop push notification and plays an audio chime."""
    t_json = json.dumps(title)
    b_json = json.dumps(body)
    sound_js = """
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(587.33, ctx.currentTime);
            osc.frequency.setValueAtTime(880, ctx.currentTime + 0.15);
            gain.gain.setValueAtTime(0.2, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.4);
        } catch(e) {}
    """ if play_sound else ""

    html_code = f"""
    <script>
    (function() {{
        {sound_js}
        function sendNotif(api) {{
            try {{
                new api({t_json}, {{
                    body: {b_json},
                    icon: "https://img.icons8.com/fluency/96/pill.png"
                }});
            }} catch(e) {{
                console.warn("Notification error:", e);
            }}
        }}

        let api = null;
        try {{
            if (window.parent && window.parent.Notification) api = window.parent.Notification;
            else if (window.Notification) api = window.Notification;
        }} catch(e) {{
            if (window.Notification) api = window.Notification;
        }}

        if (!api) return;

        if (api.permission === "granted") {{
            sendNotif(api);
        }} else if (api.permission !== "denied") {{
            api.requestPermission().then(function(perm) {{
                if (perm === "granted") sendNotif(api);
            }});
        }}
    }})();
    </script>
    """
    components.html(html_code, height=0)


def render_notification_permission_helper():
    """Renders a button that prompts the browser for notification permissions."""
    btn_html = """
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin-bottom: 6px;">
        <button id="btn-allow-notif" style="
            background: #2563eb;
            color: white;
            border: none;
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 500;
            border-radius: 6px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        ">
            🔔 Enable Browser Desktop Notifications
        </button>
        <span id="notif-status" style="margin-left: 12px; font-size: 13px; color: #4b5563;"></span>
    </div>
    <script>
    (function() {
        const btn = document.getElementById("btn-allow-notif");
        const status = document.getElementById("notif-status");
        
        let api = null;
        try {
            if (window.parent && window.parent.Notification) api = window.parent.Notification;
            else if (window.Notification) api = window.Notification;
        } catch(e) {
            if (window.Notification) api = window.Notification;
        }

        function updateUI() {
            if (!api) {
                btn.style.display = "none";
                status.innerText = "⚠️ Browser notifications not supported on this browser.";
                return;
            }
            if (api.permission === "granted") {
                btn.style.background = "#059669";
                btn.innerText = "✅ Desktop Notifications Enabled";
                btn.disabled = true;
                btn.style.cursor = "default";
                status.innerText = "Desktop alerts will pop up when doses are due.";
            } else if (api.permission === "denied") {
                btn.style.background = "#dc2626";
                btn.innerText = "⚠️ Notifications Blocked in Browser";
                btn.disabled = true;
                status.innerText = "Please unblock notifications in your browser address bar settings.";
            } else {
                btn.innerText = "🔔 Enable Browser Desktop Notifications";
                status.innerText = "Click to allow reminder popups on your screen.";
            }
        }

        btn.onclick = function() {
            if (api) {
                api.requestPermission().then(function(perm) {
                    updateUI();
                });
            }
        };

        updateUI();
    })();
    </script>
    """
    components.html(btn_html, height=45)


def load_samples() -> dict[str, str]:
    try:
        blocks = [b.strip() for b in open("sample_prescriptions.txt", encoding="utf-8").read().split("---") if b.strip()]
    except FileNotFoundError:
        blocks = []
    return {f"Sample {i + 1}: {b.splitlines()[0][:45]}": b for i, b in enumerate(blocks)}


def to_time(s: str) -> dtime:
    h, m = s.split(":")[:2]
    return dtime(int(h), int(m))


def extract_text_from_file(uploaded_file) -> str:
    """Extract plain text from uploaded .txt, .pdf, or image file."""
    name = uploaded_file.name.lower()
    raw = uploaded_file.read()

    if name.endswith(".txt"):
        return raw.decode("utf-8", errors="ignore")

    if name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=raw, filetype="pdf")
            return "\n".join(page.get_text() for page in doc).strip()
        except Exception as e:
            st.error(f"Could not read PDF: {e}")
            return ""

    if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            return pytesseract.image_to_string(img).strip()
        except ImportError:
            st.warning(
                "Image OCR requires **pytesseract** and **Tesseract** to be installed.\n\n"
                "For now, please upload a .pdf or paste prescription text directly."
            )
            return ""
        except Exception as e:
            st.error(f"Could not read image: {e}")
            return ""

    st.warning(f"Unsupported file type: `{name}`. Please upload a .txt or .pdf file.")
    return ""


def human_delta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    return f"{seconds // 60} min"


def show_table(df):
    """Renders a dataframe full-width cleanly without deprecation warnings."""
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True)



# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("💊 MedMinder AI")
    st.caption("Medicine reminders run by four cooperating AI agents.")
    demo = st.toggle("Demo mode", value=False, help="Doses start in ~30 s and timers run in seconds instead of minutes.")
    if demo:
        t = config.timings(True)
        st.info(f"First dose in {t['first_dose']}s, then every {t['gap']}s. Snooze {t['snooze']}s. Missed after {t['missed_after']}s.")
    prov = llm.provider()
    st.markdown(f"**Parser:** {'🟢 ' + prov + ' (' + llm.model_name(prov) + ')' if prov != 'none' else '🟡 offline rule-based (no API key)'}")
    st.markdown("**Reminders:** 🔔 Desktop Push Active")
    if notifier.email_configured():
        st.caption("📧 Email backup: connected")
    st.caption(f"Local time: {config.now():%d %b %Y, %H:%M:%S} ({config.TIMEZONE})")
    st.divider()
    st.caption("⚠️ Reminder tool only. Not medical advice. Always follow your doctor's instructions.")

# ---------------------------------------------------------------- agent loop (runs on every refresh)
now = config.now()
adherence_agent.sweep(demo, now)  # 1) mark stale doses missed / alert caregiver
due = reminder_agent.check_due(now)  # 2) find doses that are due

# Trigger browser push notification & audio chime when NEW doses become due
ids = {d["id"] for d in due}
new_ids = ids - st.session_state.get("seen_due", set())
if new_ids:
    new_doses = [d for d in due if d["id"] in new_ids]
    for nd in new_doses:
        trigger_desktop_notification(
            title=f"💊 Dose Due: {nd['drug']} {nd['dose']}",
            body=f"{nd['food_relation'].capitalize()} · Scheduled at {nd['scheduled_time'][11:16]}"
        )
st.session_state["seen_due"] = ids

st.header("Today's reminders")
if not due:
    st.success("Nothing due right now. You're all caught up.")
else:
    st.error(f"{len(due)} dose(s) due now")
    for d in due:
        overdue = (now - db.from_iso(d["scheduled_time"])).total_seconds()
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([5, 1.2, 1.2, 1.2])
            snoozed = f" · snoozed {d['snooze_count']}×" if d["snooze_count"] else ""
            c1.markdown(
                f"### {d['drug']} {d['dose']}\n"
                f"{d['food_relation'].capitalize()} · due {d['scheduled_time'][11:16]} · waiting {human_delta(overdue)}{snoozed}"
            )
            if c2.button("✅ Taken", key=f"take_{d['id']}", type="primary"):
                adherence_agent.take(d["id"])
                st.rerun()
            if c3.button("⏰ Snooze", key=f"snooze_{d['id']}"):
                adherence_agent.snooze(d["id"], demo)
                st.rerun()
            if c4.button("⏭️ Skip", key=f"skip_{d['id']}"):
                adherence_agent.skip(d["id"])
                st.rerun()

# ---------------------------------------------------------------- tabs
tab_add, tab_up, tab_adh, tab_log, tab_set = st.tabs(
    ["Add prescription", "Upcoming doses", "Adherence", "Agent activity", "Settings"]
)

# ---- Add prescription
with tab_add:
    st.subheader("Add a prescription")

    # ---- File upload ----
    st.markdown("**📎 Upload prescription file** *(supports .pdf, .txt, image)*")
    uploaded = st.file_uploader(
        "Upload prescription file",
        type=["pdf", "txt", "png", "jpg", "jpeg"],
        label_visibility="collapsed",
        help="Upload a PDF or text file prescription. The text is automatically extracted below.",
    )
    if uploaded is not None:
        extracted = extract_text_from_file(uploaded)
        if extracted:
            st.session_state["rx_text"] = extracted
            st.success(f"✅ Text extracted from `{uploaded.name}` — review below before parsing.")

    st.markdown("**✏️ Or type / paste prescription text**")
    samples = load_samples()

    def _load_sample():
        choice = st.session_state.get("sample_choice")
        if choice in samples:
            st.session_state["rx_text"] = samples[choice]

    st.selectbox("Load a sample", ["Choose a sample…", *samples], key="sample_choice", on_change=_load_sample)
    text = st.text_area(
        "Prescription text",
        key="rx_text",
        height=150,
        placeholder="Paracetamol 500mg twice daily after food for 5 days\nAmoxicillin 250mg three times a day for 7 days",
    )
    if st.button("Parse with AI agent", type="primary"):
        try:
            with st.spinner("Parser agent is reading the prescription…"):
                st.session_state["parsed"] = parser_agent.parse_prescription(text)
                st.session_state["parsed_text"] = text
        except Exception as e:
            st.session_state["parsed"] = None
            st.error(str(e))

    res = st.session_state.get("parsed")
    if res:
        st.markdown(f"**Parsed by:** `{res.source}`")
        if res.warning:
            st.warning(res.warning)
        df = pd.DataFrame([m.model_dump() for m in res.prescription.medicines])
        df["slots"] = df["slots"].apply(", ".join)
        show_table(df.drop(columns=["frequency_per_day"]))
        st.caption("Check the medicines, doses and timings above against the paper prescription before confirming.")
        with st.expander("Raw JSON"):
            st.json(res.prescription.model_dump())
        if st.button("Confirm and create reminders", type="primary"):
            pid = db.add_prescription(st.session_state["parsed_text"], res.prescription.model_dump())
            n = scheduler_agent.create_schedule(pid, res.prescription.medicines, db.get_user(), demo)
            st.session_state["parsed"] = None
            st.success(f"Created {n} reminder(s). See the Upcoming doses tab.")
            if demo:
                st.info("Demo mode: the first reminder appears at the top in about 30 seconds.")

# ---- Upcoming
with tab_up:
    st.subheader("Upcoming doses")
    rows = db.upcoming_doses(db.iso(now), 60)
    if rows:
        show_table(
            pd.DataFrame(rows)[["scheduled_time", "drug", "dose", "food_relation"]].rename(
                columns={"scheduled_time": "When", "drug": "Medicine", "dose": "Dose", "food_relation": "Food"}
            )
        )
    else:
        st.info("No upcoming doses yet. Add a prescription to get started.")

# ---- Adherence
with tab_adh:
    st.subheader("Adherence")
    s = adherence_agent.stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Adherence", f"{s['adherence_pct']}%" if s["adherence_pct"] is not None else "–")
    c2.metric("Taken", s["taken"])
    c3.metric("Skipped", s["skipped"])
    c4.metric("Missed", s["missed"])
    hist = db.resolved_doses()
    if hist:
        h = pd.DataFrame(hist)
        h["day"] = h["scheduled_time"].str[:10]
        st.bar_chart(h.pivot_table(index="day", columns="status", values="id", aggfunc="count", fill_value=0))
        show_table(
            h[["scheduled_time", "drug", "dose", "status", "snooze_count"]].rename(
                columns={"scheduled_time": "Scheduled", "drug": "Medicine", "dose": "Dose", "status": "Status", "snooze_count": "Snoozes"}
            )
        )
    else:
        st.info("History appears here once you respond to (or miss) a dose.")

# ---- Agent activity
with tab_log:
    st.subheader("What the agents did")
    ev = db.recent_events(100)
    if ev:
        show_table(pd.DataFrame(ev))
    else:
        st.info("No agent activity yet.")

# ---- Settings
with tab_set:
    st.subheader("Settings & Reminders")

    st.markdown("#### 🔔 Desktop Push Notifications")
    render_notification_permission_helper()

    col_t1, col_t2 = st.columns([1.5, 4])
    if col_t1.button("🔔 Send Test Push Notification", type="secondary"):
        trigger_desktop_notification(
            title="💊 MedMinder AI Test",
            body="Desktop push notifications are active! You'll be alerted when doses are due."
        )
        col_t2.success("Sent test notification banner to your desktop & played chime! ✅")

    st.divider()
    st.markdown("#### 👤 Profile & Daily Meal Routine")
    u = db.get_user()
    with st.form("settings"):
        name = st.text_input("Patient name", u["name"])
        c1, c2, c3, c4 = st.columns(4)
        b = c1.time_input("Breakfast", to_time(u["breakfast_time"]))
        l = c2.time_input("Lunch", to_time(u["lunch_time"]))
        d = c3.time_input("Dinner", to_time(u["dinner_time"]))
        z = c4.time_input("Bedtime", to_time(u["bedtime"]))
        if st.form_submit_button("Save routine", type="primary"):
            db.update_user(
                name=name.strip() or "Patient",
                breakfast_time=b.strftime("%H:%M"),
                lunch_time=l.strftime("%H:%M"),
                dinner_time=d.strftime("%H:%M"),
                bedtime=z.strftime("%H:%M"),
            )
            st.success("Routine saved. New prescriptions will automatically align to these times.")

    with st.expander("📧 Email Notifications (Optional Backup)", expanded=notifier.email_configured()):
        st.caption(
            "Email reminders are an optional backup. "
            "If Gmail App Passwords aren't enabled on your account, Browser Desktop Notifications handle all reminders above."
        )
        with st.form("email_settings"):
            em = st.text_input("Patient email", u["email"], placeholder="patient@example.com")
            cg = st.text_input("Caregiver email (alerted if 2 consecutive doses missed)", u["caregiver_email"], placeholder="caregiver@example.com")
            if st.form_submit_button("Save email addresses"):
                db.update_user(email=em.strip(), caregiver_email=cg.strip())
                st.success("Email preferences saved.")

        if notifier.email_configured():
            if st.button("Send test email"):
                ok, info = notifier.send_email(db.get_user()["email"], "MedMinder AI test", "Email reminders are working.")
                (st.success if ok else st.error)(f"Test email: {info}")
        else:
            st.info("ℹ️ SMTP is not configured in `.env`. Browser Desktop Notifications are active and handle your reminders.")

    st.divider()
    with st.expander("🗑️ Reset / Clear All Data"):
        st.warning("This will erase all active prescriptions, dose schedules, and adherence history.")
        if st.checkbox("Yes, delete all data"):
            if st.button("Reset Database", type="primary"):
                db.reset_all()
                st.session_state.clear()
                st.rerun()
