# gsheets.py
import os, datetime
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

LOCKUPS_SHEET = "Lockups"
LOCKUP_LOGS_SHEET = "LockupLogs"
EVENTS_SHEET = "Events"
EVENT_LOGS_SHEET = "EventLogs"

LOCKUPS_HEADERS = ["id","ticker","account","quantity","lockup_start","lockup_end","notes","chat_id"]
LOCKUP_LOGS_HEADERS = ["lockup_id","stage","yyyymmdd"]
EVENTS_HEADERS = ["id","issuer","event_type","event_date","event_time","notes","chat_id","alert_offsets"]
EVENT_LOGS_HEADERS = ["event_id","stage","yyyymmddHHMM"]

def _client():
    creds = Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)

def _open():
    return _client().open_by_key(SPREADSHEET_ID)

def ws(name:str):
    return _open().worksheet(name)

def ensure_headers():
    for name, headers in [
        (LOCKUPS_SHEET, LOCKUPS_HEADERS),
        (LOCKUP_LOGS_SHEET, LOCKUP_LOGS_HEADERS),
        (EVENTS_SHEET, EVENTS_HEADERS),
        (EVENT_LOGS_SHEET, EVENT_LOGS_HEADERS),
    ]:
        w = ws(name)
        vals = w.row_values(1)
        if [v.strip() for v in vals] != headers:
            w.clear()
            w.append_row(headers, value_input_option="RAW")

# -------- Lockups ----------
def _to_date(s):  # YYYY-MM-DD
    return datetime.date.fromisoformat(str(s).strip())

def read_lockups():
    rows = ws(LOCKUPS_SHEET).get_all_records()
    out=[]
    for r in rows:
        if not r.get("id"):
            continue
        try:
            out.append({
                "id": int(r["id"]),
                "ticker": str(r["ticker"]).strip(),
                "account": str(r["account"]).strip(),
                "quantity": int(str(r["quantity"]).replace(",","").strip()),
                "lockup_start": _to_date(r["lockup_start"]),
                "lockup_end": _to_date(r["lockup_end"]),
                "notes": str(r.get("notes","")).strip(),
                "chat_id": str(r["chat_id"]).strip(),
            })
        except Exception:
            pass
    return out

def next_lockup_id():
    col = ws(LOCKUPS_SHEET).col_values(1)
    try:
        last = max(int(v) for v in col[1:] if v.strip())
        return last+1
    except:
        return 1

def add_lockup(row:dict)->int:
    w = ws(LOCKUPS_SHEET)
    _id = next_lockup_id()
    payload = [
        _id, row["ticker"], row["account"], row["quantity"],
        row["lockup_start"], row["lockup_end"], row.get("notes",""),
        row["chat_id"]
    ]
    w.append_row(payload, value_input_option="RAW")
    return _id

def exists_lockup_log(lockup_id:int, stage:str, yyyymmdd:str)->bool:
    rows = ws(LOCKUP_LOGS_SHEET).get_all_records()
    for r in rows:
        try:
            if int(r["lockup_id"])==lockup_id and r["stage"]==stage and r["yyyymmdd"]==yyyymmdd:
                return True
        except:
            continue
    return False

def append_lockup_log(lockup_id:int, stage:str, yyyymmdd:str):
    ws(LOCKUP_LOGS_SHEET).append_row([lockup_id, stage, yyyymmdd], value_input_option="RAW")

# -------- Events ----------
def _parse_time(s):
    s = (s or "").strip()
    if not s:
        return None
    hh, mm = s.split(":")
    return int(hh), int(mm)

def _parse_offsets(s):
    s = (s or "").strip()
    if not s:
        return [0]
    outs=[]
    for t in s.split(","):
        t=t.strip()
        if not t: 
            continue
        outs.append(int(t))
    return outs

def read_events():
    rows = ws(EVENTS_SHEET).get_all_records()
    out=[]
    for r in rows:
        if not r.get("id"):
            continue
        try:
            out.append({
                "id": int(r["id"]),
                "issuer": str(r["issuer"]).strip(),
                "event_type": str(r["event_type"]).strip(),
                "event_date": _to_date(r["event_date"]),
                "event_time": _parse_time(r.get("event_time","")),  # None or (hh,mm)
                "notes": str(r.get("notes","")).strip(),
                "chat_id": str(r["chat_id"]).strip(),
                "alert_offsets": _parse_offsets(r.get("alert_offsets","")),
            })
        except Exception:
            pass
    return out

def next_event_id():
    col = ws(EVENTS_SHEET).col_values(1)
    try:
        last = max(int(v) for v in col[1:] if v.strip())
        return last+1
    except:
        return 1

def add_event(row:dict)->int:
    w = ws(EVENTS_SHEET)
    _id = next_event_id()
    payload = [
        _id, row["issuer"], row["event_type"], row["event_date"],
        row.get("event_time",""), row.get("notes",""), row["chat_id"],
        row.get("alert_offsets","0")
    ]
    w.append_row(payload, value_input_option="RAW")
    return _id

def exists_event_log(event_id:int, stage:str, key:str)->bool:
    rows = ws(EVENT_LOGS_SHEET).get_all_records()
    for r in rows:
        try:
            if int(r["event_id"])==event_id and r["stage"]==stage and r["yyyymmddHHMM"]==key:
                return True
        except:
            continue
    return False

def append_event_log(event_id:int, stage:str, key:str):
    ws(EVENT_LOGS_SHEET).append_row([event_id, stage, key], value_input_option="RAW")
