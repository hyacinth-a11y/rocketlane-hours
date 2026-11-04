"""Rocketlane Hours: sync timesheet hours from Rocketlane and export per-project PDF reports.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import os
import random
import zipfile
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import store
from report import build_pdf
from rocketlane import RocketlaneClient, RocketlaneError

st.set_page_config(page_title="Rocketlane Hours", page_icon="⏱", layout="wide")


# ================================================================ config & secrets
def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, os.environ.get(name, ""))
    except Exception:
        return os.environ.get(name, "")


settings = store.load_settings()
SECRETS_KEY = _secret("ROCKETLANE_API_KEY")
# A key saved in the app's Settings wins; the one in Secrets is the permanent fallback.
API_KEY = settings["api_key"] or SECRETS_KEY
KEY_SOURCE = "Settings" if settings["api_key"] else ("Secrets" if SECRETS_KEY else None)
APP_PASSWORD = _secret("APP_PASSWORD")


# ================================================================ password gate
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


# ================================================================ sample data (no API key yet)
def demo_data(since: date) -> tuple[list[dict], list[dict]]:
    rnd = random.Random(42)
    projects = [
        {"id": 1, "name": "Acme Corp – AWS Migration", "customer": "Acme Corp", "status": "In progress", "archived": False},
        {"id": 2, "name": "Globex – Data Platform", "customer": "Globex", "status": "In progress", "archived": False},
        {"id": 3, "name": "Initech – Cost Optimization", "customer": "Initech", "status": "Completed", "archived": False},
    ]
    people = ["Maria Santos", "James Lee", "Priya Nair", "Tom Becker", "Ana Cruz"]
    tasks = ["Discovery workshop", "Architecture design", "Landing zone setup",
             "Migration wave 1", "Testing & validation", "Project management"]
    days = (date.today() - since).days + 1
    entries = []
    for p in projects:
        team = rnd.sample(people, 3)
        for _ in range(days // 3):
            t = rnd.choice(tasks)
            entries.append({
                "id": rnd.randint(1, 10**9), "date": (since + timedelta(days=rnd.randrange(days))).isoformat(),
                "project_id": p["id"], "project": p["name"], "person": rnd.choice(team), "email": "",
                "task": t, "phase": "", "hours": rnd.choice([0.5, 1, 1.5, 2, 3, 4, 6, 8]),
                "billable": t != "Project management" or rnd.random() < 0.3,
                "status": rnd.choice(["APPROVED", "SUBMITTED"]), "user_id": None,
                "notes": rnd.choice(["", "Client call", "Follow-ups from review", "Config + docs"])})
    return projects, entries


# ================================================================ sync
def run_sync() -> None:
    months = int(settings["sync_months"])
    since = date.today() - timedelta(days=months * 31)
    if not API_KEY:
        projects, entries = demo_data(since)
        store.save_sync(projects, entries, since, "sample")
        return
    client = RocketlaneClient(API_KEY)
    bar = st.progress(0.0, text="Syncing projects…")
    projects = client.projects(include_archived=True)
    bar.progress(0.05, text=f"{len(projects)} projects. Syncing time entries…")

    def progress(done, total):
        bar.progress(min(0.05 + 0.95 * done / max(total, 1), 1.0),
                     text=f"Syncing time entries… {done:,} of {total:,}")

    entries = client.all_time_entries(since, progress=progress)
    names = {p["id"]: p["name"] for p in projects}
    for e in entries:
        e["project"] = e["project"] or names.get(e["project_id"], "Unknown project")
    store.save_sync(projects, entries, since, "rocketlane")
    bar.empty()


def ago(iso: str) -> str:
    mins = int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 60 * 24:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} days ago"


# ================================================================ sidebar
with st.sidebar:
    st.header("⏱ Rocketlane Hours")
    page = st.radio("Menu", ["Projects", "Settings"], label_visibility="collapsed")
    st.divider()
    if st.button("🔄 Sync with Rocketlane" if API_KEY else "🔄 Load sample data",
                 type="primary", width="stretch"):
        try:
            run_sync()
            st.session_state.pop("pdf", None)
            st.toast("Sync complete")
            st.rerun()
        except RocketlaneError as e:
            st.error(str(e))
    info = store.sync_info()
    if info:
        st.caption(f"Last synced {ago(info['synced_at'])} · {int(info['projects'])} projects · "
                   f"{int(info['entries']):,} entries since {info['since']}")
    st.caption("✅ Connected to Rocketlane" if API_KEY else "⚠️ No API key yet. Add one in Settings.")
    if APP_PASSWORD and st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

info = store.sync_info()
if info and info["source"] == "sample":
    st.info("**Sample data.** These projects and hours are made up. Add your Rocketlane API key in "
            "**Settings**, then click **Sync**.", icon="🧪")


# ================================================================ SETTINGS PAGE
if page == "Settings":
    st.title("Settings")

    st.subheader("Rocketlane connection")
    if KEY_SOURCE:
        st.success(f"Connected. API key ending in **…{API_KEY[-4:]}** (saved in {KEY_SOURCE}).")
    else:
        st.warning("Not connected yet. Paste your Rocketlane API key below.")
    st.caption("Get a key in Rocketlane: Settings → API → Create API key.")
    with st.form("api_key_form", clear_on_submit=True):
        new_key = st.text_input("API key", type="password", placeholder="Paste a new key…")
        if st.form_submit_button("Save and test connection", type="primary"):
            if not new_key.strip():
                st.error("Paste a key first.")
            else:
                try:
                    RocketlaneClient(new_key)._get("/projects", {"pageSize": 1})
                    store.save_settings(api_key=new_key.strip())
                    st.session_state.key_saved = True
                    st.rerun()
                except RocketlaneError as e:
                    st.error(f"That key didn't work: {e}")
    if st.session_state.pop("key_saved", False):
        st.success("Key works and is saved. Click **Sync** in the sidebar to load your data.")
    if settings["api_key"] and st.button("Remove saved key"):
        store.save_settings(api_key="")
        st.rerun()
    st.caption("A key saved here stays until the app restarts on Streamlit Cloud. For a permanent "
               "key, also put it in your app's Secrets as `ROCKETLANE_API_KEY`.")

    st.subheader("Sync")
    choices = {3: "Last 3 months", 6: "Last 6 months", 12: "Last 12 months", 24: "Last 2 years"}
    cur = int(settings["sync_months"])
    months = st.selectbox("Time entries to sync", list(choices), format_func=choices.get,
                          index=list(choices).index(cur) if cur in choices else 2,
                          help="More history means a slower sync.")
    st.subheader("Reports")
    company = st.text_input("Company name on PDF footer", value=settings["company"])
    if st.button("Save settings"):
        store.save_settings(sync_months=months, company=company)
        st.success("Saved.")

    st.subheader("Data")
    if info and st.button("Clear synced data"):
        store.clear()
        st.rerun()
    st.stop()


# ================================================================ PROJECTS
if not info:
    st.title("Projects")
    st.info("No data yet. Click **Sync** in the sidebar to pull your projects and hours from Rocketlane."
            if API_KEY else
            "No data yet. Add your API key in **Settings**, or click **Load sample data** to try the app.")
    st.stop()

company = settings["company"]
projects = store.projects()
by_id = {int(r.id): r for r in projects.itertuples()}


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
        picked = st.date_input("From – to", value=ranges["Last month"], format="YYYY-MM-DD",
                               key=f"{key}_custom")
        if not (isinstance(picked, tuple) and len(picked) == 2):
            st.caption("Pick an end date.")
            st.stop()
        start, end = picked
    else:
        start, end = ranges[preset]
        st.caption(f"{start:%d %b %Y} → {end:%d %b %Y}")
    if str(start) < info["since"]:
        st.warning(f"Synced data starts {info['since']}. Earlier hours aren't included. "
                   "Sync more history in Settings.")
    return start, end


def file_stamp(name: str, start: date, end: date) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    return f"{safe}_{start:%Y%m%d}-{end:%Y%m%d}"


def open_project(pid):
    st.session_state.project_id = pid
    st.session_state.pop("pdf", None)


selected = st.session_state.get("project_id")

# ---------------------------------------------------------------- project list
if selected not in by_id:
    st.title("Projects")
    summary = store.project_summary()
    table = projects.merge(summary, left_on="id", right_on="project_id", how="left")
    table["hours"] = table["hours"].fillna(0).round(1)

    c1, c2, c3, c4 = st.columns([3, 2, 2, 1.3], vertical_alignment="bottom")
    q = c1.text_input("Search projects or clients", placeholder="Type to filter…")
    statuses = sorted(s for s in table["status"].dropna().unique() if s)
    sel_status = c2.multiselect("Status", statuses, placeholder="All statuses")
    clients = sorted(s for s in table["customer"].dropna().unique() if s)
    sel_client = c3.multiselect("Client", clients, placeholder="All clients")
    show_archived = c4.checkbox("Show archived")
    only_hours = c4.checkbox("Only with hours", value=True)

    t = table
    if q:
        ql = q.lower()
        t = t[t["name"].str.lower().str.contains(ql, regex=False)
              | t["customer"].fillna("").str.lower().str.contains(ql, regex=False)]
    if sel_status:
        t = t[t["status"].isin(sel_status)]
    if sel_client:
        t = t[t["customer"].isin(sel_client)]
    if not show_archived:
        t = t[~t["archived"].astype(bool)]
    if only_hours:
        t = t[t["hours"] > 0]
    t = t.sort_values("name").reset_index(drop=True)

    st.caption(f"{len(t)} of {len(table)} projects. **Click a row to open it.**")
    pick = st.dataframe(
        t[["name", "customer", "status", "hours", "last_logged"]],
        column_config={"name": "Project", "customer": "Client", "status": "Status",
                       "hours": st.column_config.NumberColumn("Hours synced", format="%.1f"),
                       "last_logged": "Last time logged"},
        hide_index=True, width="stretch", height=480,
        on_select="rerun", selection_mode="single-row", key="project_table")
    rows = pick.selection.rows if pick and pick.selection else []
    if rows:
        open_project(int(t.iloc[rows[0]]["id"]))
        st.rerun()

    with st.expander("Bulk export: reports for several projects at once"):
        names = st.multiselect("Projects", t["name"].tolist(), placeholder="Choose projects…")
        b_start, b_end = date_range_picker("bulk")
        if names and st.button("Generate reports", type="primary"):
            df = store.entries(start=b_start, end=b_end)
            zbuf, made = io.BytesIO(), 0
            with zipfile.ZipFile(zbuf, "w") as z:
                for name in names:
                    part = df[df.project == name] if not df.empty else df
                    if part.empty:
                        continue
                    cust = t.loc[t.name == name, "customer"].iloc[0]
                    z.writestr(f"{file_stamp(name, b_start, b_end)}.pdf",
                               build_pdf(part, b_start, b_end, {}, company=company, client=cust or ""))
                    made += 1
            skipped = len(names) - made
            st.success(f"{made} report(s) ready." + (f" {skipped} had no hours in this range." if skipped else ""))
            if made:
                st.download_button("⬇ Download all (.zip)", zbuf.getvalue(),
                                   file_name=f"timesheets_{b_start:%Y%m%d}-{b_end:%Y%m%d}.zip",
                                   mime="application/zip")
    st.stop()

# ---------------------------------------------------------------- project page
proj = by_id[selected]
if st.button("← All projects"):
    open_project(None)
    st.session_state.pop("project_table", None)
    st.rerun()
st.title(proj.name)
st.caption(" · ".join(x for x in [f"Client: {proj.customer}" if proj.customer else "", proj.status or ""] if x))

c1, _ = st.columns([2, 3])
with c1:
    start, end = date_range_picker("proj")

df = store.entries(selected, start, end)
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

sig = (selected, start, end, tuple(sel_people), tuple(sel_tasks), hrange, billing, detail, notes, split,
       info["synced_at"])
if st.session_state.get("pdf_sig") != sig:  # settings or data changed: old PDF is stale
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
                                     show_notes=notes, billable_split=split, client=proj.customer or "")
    st.session_state.pdf_name = f"{file_stamp(proj.name, start, end)}.pdf"

if st.session_state.get("pdf"):
    st.download_button("⬇ Download PDF", st.session_state.pdf, file_name=st.session_state.pdf_name,
                       mime="application/pdf")
