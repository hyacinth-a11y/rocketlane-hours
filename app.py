"""Rocketlane Hours: pull timesheet hours per project and export PDF reports.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import os
import random
import zipfile
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from report import build_pdf
from rocketlane import RocketlaneClient, RocketlaneError

st.set_page_config(page_title="Rocketlane Hours", page_icon="⏱", layout="wide")


# ---------------------------------------------------------------- data access
def _secret_key() -> str:
    try:
        return st.secrets.get("ROCKETLANE_API_KEY", os.environ.get("ROCKETLANE_API_KEY", ""))
    except Exception:
        return os.environ.get("ROCKETLANE_API_KEY", "")


@st.cache_data(ttl=600, show_spinner="Loading projects…")
def load_projects(api_key: str, archived: bool) -> list[dict]:
    return RocketlaneClient(api_key).projects(include_archived=archived)


@st.cache_data(ttl=300, show_spinner="Pulling time entries…")
def load_entries(api_key: str, project_ids: tuple, start: date, end: date) -> pd.DataFrame:
    client = RocketlaneClient(api_key)
    rows = []
    for pid in project_ids:
        rows += client.time_entries(pid, start, end)
    return pd.DataFrame(rows)


def demo_projects() -> list[dict]:
    names = ["Acme Corp – AWS Migration", "Globex – Data Platform", "Initech – Cost Optimization"]
    return [{"id": i + 1, "name": n, "customer": n.split(" – ")[0]} for i, n in enumerate(names)]


def demo_entries(project_ids: tuple, start: date, end: date) -> pd.DataFrame:
    rnd = random.Random(42)
    people = ["Maria Santos", "James Lee", "Priya Nair", "Tom Becker", "Ana Cruz"]
    tasks = ["Discovery workshop", "Architecture design", "Landing zone setup",
             "Migration wave 1", "Testing & validation", "Project management"]
    projects = {p["id"]: p["name"] for p in demo_projects()}
    rows, days = [], max((end - start).days, 0) + 1
    for pid in project_ids:
        team = rnd.sample(people, 3)
        for _ in range(12 + days // 2):
            d = start + timedelta(days=rnd.randrange(days))
            t = rnd.choice(tasks)
            rows.append({"id": rnd.randint(1, 10**6), "date": d.isoformat(), "project_id": pid,
                         "project": projects[pid], "person": rnd.choice(team), "email": "",
                         "task": t, "phase": "", "hours": rnd.choice([0.5, 1, 1.5, 2, 3, 4, 6, 8]),
                         "billable": t != "Project management" or rnd.random() < 0.3,
                         "status": rnd.choice(["APPROVED", "SUBMITTED"]),
                         "notes": rnd.choice(["", "Client call", "Follow-ups from review", "Config + docs"])})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- password gate
def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, os.environ.get(name, ""))
    except Exception:
        return os.environ.get(name, "")


APP_PASSWORD = _secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("⏱ Rocketlane Hours")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if pw == APP_PASSWORD:
                st.session_state.authed = True
                st.rerun()
            st.error("Wrong password.")
    st.stop()

# ---------------------------------------------------------------- sidebar
SAVED_KEY = _secret_key()
with st.sidebar:
    st.header("⏱ Rocketlane Hours")
    if SAVED_KEY:
        # Key is stored on the server: never show it or let viewers change it.
        demo, api_key = False, SAVED_KEY
        st.caption("✅ Connected to Rocketlane")
    else:
        demo = st.toggle("Demo mode (sample data)", value=True,
                         help="Try the app without an API key.")
        api_key = "" if demo else st.text_input(
            "Rocketlane API key", type="password",
            help="Rocketlane → Settings → API → Create API key")
    archived = st.checkbox("Include archived projects", value=False)
    company = st.text_input("Company name on PDF", value="nClouds")
    if APP_PASSWORD and st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

if demo:
    st.info("**Sample data.** This app isn't connected to Rocketlane yet, so the projects and hours "
            "below are made up. Add the API key in the app's Secrets to switch to real data.", icon="🧪")

if not demo and not api_key:
    st.info("Enter your Rocketlane API key in the sidebar, or turn on demo mode.")
    st.stop()

try:
    projects = demo_projects() if demo else load_projects(api_key, archived)
except RocketlaneError as e:
    st.error(str(e))
    st.stop()

if not projects:
    st.warning("No projects found for this API key.")
    st.stop()

id_to_name = {p["id"]: p["name"] for p in projects}
by_id = {p["id"]: p for p in projects}


# ---------------------------------------------------------------- shared helpers
def date_range_picker(key: str) -> tuple[date, date]:
    today = date.today()
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    ranges = {
        "Last month": (last_month_end.replace(day=1), last_month_end),
        "This month": (first_this, today),
        "Last week": (week_start - timedelta(days=7), week_start - timedelta(days=1)),
        "This week": (week_start, today),
        "Last 30 days": (today - timedelta(days=29), today),
        "Year to date": (today.replace(month=1, day=1), today),
    }
    preset = st.selectbox("Date range", [*ranges, "Custom"], key=f"{key}_preset")
    if preset == "Custom":
        picked = st.date_input("From – to", value=(ranges["Last month"]), format="YYYY-MM-DD",
                               key=f"{key}_custom")
        if isinstance(picked, tuple) and len(picked) == 2:
            return picked
        st.caption("Pick an end date.")
        st.stop()
    start, end = ranges[preset]
    st.caption(f"{start:%d %b %Y} → {end:%d %b %Y}")
    return start, end


def get_entries(ids: tuple, start: date, end: date) -> pd.DataFrame:
    try:
        df = demo_entries(ids, start, end) if demo else load_entries(api_key, ids, start, end)
    except RocketlaneError as e:
        st.error(str(e))
        st.stop()
    if not df.empty:
        df["project"] = df.apply(
            lambda r: r["project"] or id_to_name.get(r["project_id"], "Unknown project"), axis=1)
    return df


def file_stamp(name: str, start: date, end: date) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    return f"{safe}_{start:%Y%m%d}-{end:%Y%m%d}"


def open_project(pid):
    st.session_state.project_id = pid
    st.session_state.pop("pdf", None)


selected = st.session_state.get("project_id")

# ================================================================ PROJECT LIST
if selected not in by_id:
    st.title("Projects")
    st.caption(f"{len(projects)} projects from Rocketlane. Open one to pull its hours report.")

    q = st.text_input("Search projects or clients", placeholder="Type to filter…")
    shown = [p for p in projects
             if not q or q.lower() in p["name"].lower() or q.lower() in (p["customer"] or "").lower()]

    head = st.columns([5, 3, 1.2])
    head[0].markdown("**Project**")
    head[1].markdown("**Client**")
    for p in shown:
        row = st.columns([5, 3, 1.2], vertical_alignment="center")
        row[0].write(p["name"])
        row[1].write(p["customer"] or "—")
        row[2].button("Open", key=f"open_{p['id']}", on_click=open_project, args=(p["id"],),
                      width="stretch")
    if not shown:
        st.info("No projects match that search.")

    # Bulk export for month-end: many projects in one go.
    with st.expander("Bulk export: reports for several projects at once"):
        names = st.multiselect("Projects", [p["name"] for p in shown], placeholder="Choose projects…")
        b_start, b_end = date_range_picker("bulk")
        if names and st.button("Generate reports", type="primary"):
            ids = tuple(p["id"] for p in projects if p["name"] in names)
            df = get_entries(ids, b_start, b_end)
            zbuf, made = io.BytesIO(), 0
            with zipfile.ZipFile(zbuf, "w") as z:
                for name in names:
                    part = df[df.project == name] if not df.empty else df
                    if part.empty:
                        continue
                    z.writestr(f"{file_stamp(name, b_start, b_end)}.pdf",
                               build_pdf(part, b_start, b_end, {}, company=company))
                    made += 1
            skipped = len(names) - made
            st.success(f"{made} report(s) ready." + (f" {skipped} project(s) had no hours in this range." if skipped else ""))
            if made:
                st.download_button("⬇ Download all (.zip)", zbuf.getvalue(),
                                   file_name=f"timesheets_{b_start:%Y%m%d}-{b_end:%Y%m%d}.zip",
                                   mime="application/zip")
    st.stop()

# ================================================================ PROJECT PAGE
proj = by_id[selected]
st.button("← All projects", on_click=open_project, args=(None,))
st.title(proj["name"])
if proj["customer"]:
    st.caption(f"Client: {proj['customer']}")

c1, _ = st.columns([2, 3])
with c1:
    start, end = date_range_picker("proj")

df = get_entries((proj["id"],), start, end)
if df.empty:
    st.warning("No hours were logged on this project in this date range.")
    st.stop()

st.subheader("Filters")
f1, f2, f3, f4 = st.columns([2, 2, 2, 1.4])
with f1:
    people = sorted(df.person.unique())
    sel_people = st.multiselect("Person", people, default=people,
                                help="Everyone who logged time on this project in this range.")
with f2:
    tasks = sorted(df.task.unique())
    sel_tasks = st.multiselect("Task", tasks, default=tasks)
with f3:
    hmax = float(max(df.hours.max(), 0.5))
    hrange = st.slider("Hours per entry", 0.0, hmax, (0.0, hmax), step=0.25)
with f4:
    billing = st.radio("Billable", ["All", "Billable", "Non-billable"])

view = df[df.person.isin(sel_people) & df.task.isin(sel_tasks) & df.hours.between(*hrange)]
if billing == "Billable":
    view = view[view.billable]
elif billing == "Non-billable":
    view = view[~view.billable]

bill = view.loc[view.billable, "hours"].sum()
k = st.columns(4)
k[0].metric("Total hours", f"{view.hours.sum():,.2f}")
k[1].metric("Billable", f"{bill:,.2f}")
k[2].metric("People", view.person.nunique())
k[3].metric("Entries", len(view))

t1, t2, t3 = st.tabs(["By person", "By task", "Entry log"])
with t1:
    st.dataframe(view.groupby("person").hours.sum().sort_values(ascending=False).rename("Hours"),
                 width="stretch")
with t2:
    st.dataframe(view.groupby("task").hours.sum().sort_values(ascending=False).rename("Hours"),
                 width="stretch")
with t3:
    st.dataframe(view[["date", "person", "task", "hours", "billable", "notes"]].sort_values("date"),
                 width="stretch", hide_index=True)

st.subheader("Report")
o1, o2, o3 = st.columns(3)
detail = o1.checkbox("Include time entry log", value=True)
notes = o2.checkbox("Include notes", value=True, disabled=not detail)
split = o3.checkbox("Show billable / non-billable split", value=True)

sig = (start, end, tuple(sel_people), tuple(sel_tasks), hrange, billing, detail, notes, split)
if st.session_state.get("pdf_sig") != sig:  # settings changed: old PDF is stale
    st.session_state.pop("pdf", None)

if st.button("Generate report", type="primary", disabled=view.empty):
    st.session_state.pdf_sig = sig
    filters = {
        "People": None if set(sel_people) == set(people) else ", ".join(sel_people),
        "Tasks": None if set(sel_tasks) == set(tasks) else ", ".join(sel_tasks),
        "Hours/entry": None if hrange == (0.0, hmax) else f"{hrange[0]}–{hrange[1]}",
        "Billable": None if billing == "All" else billing,
    }
    st.session_state.pdf = build_pdf(view, start, end, filters, detail, company,
                                     show_notes=notes, billable_split=split,
                                     client=proj["customer"])
    st.session_state.pdf_name = f"{file_stamp(proj['name'], start, end)}.pdf"

if st.session_state.get("pdf"):
    st.download_button("⬇ Download PDF", st.session_state.pdf, file_name=st.session_state.pdf_name,
                       mime="application/pdf")
