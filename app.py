# app.py
import os, datetime
from dotenv import load_dotenv
from pytz import timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler

from gsheets import (
    ensure_headers, read_lockups, add_lockup, exists_lockup_log, append_lockup_log,
    read_events, add_event, exists_event_log, append_event_log
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED = {int(x) for x in os.getenv("ALLOWED_CHAT_IDS","").split(",") if x}
KST = timezone("Asia/Seoul")

def is_allowed(chat_id:int)->bool:
    return (not ALLOWED) or (chat_id in ALLOWED)

def dday(target:datetime.date, today:datetime.date)->int:
    return (target - today).days

async def _send(app:Application, chat_id:int, text:str):
    await app.bot.send_message(chat_id=chat_id, text=text)

# ---------- Commands ----------
async def cmd_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    await update.message.reply_text("확약·이벤트 알림 봇 준비 완료. /help 로 명령어 확인")

async def cmd_help(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    await update.message.reply_text(
        "/myid\n"
        "/add_lockup TICKER,ACCOUNT,QUANTITY,YYYY-MM-DD,YYYY-MM-DD,NOTES\n"
        "/list_lockup\n"
        "/add_event ISSUER,EVENT_TYPE,YYYY-MM-DD[,HH:MM][,ALERT_OFFSETS][- NOTES]\n"
        "  예) /add_event 삼진식품,수요-시작,2025-11-19,09:00,-1,0\n"
        "/list_event"
    )

async def cmd_myid(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id = {update.effective_chat.id}")

async def cmd_add_lockup(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    try:
        raw = " ".join(context.args)
        parts = [p.strip() for p in raw.split(",")]
        ticker, account, qty, sday, eday, *rest = parts + [""]
        notes = rest[0] if rest else ""
        _id = add_lockup({
            "ticker": ticker,
            "account": account,
            "quantity": int(qty),
            "lockup_start": sday,
            "lockup_end": eday,
            "notes": notes,
            "chat_id": str(update.effective_chat.id)
        })
        await update.message.reply_text(f"[Lockup 등록] id={_id} / {ticker}/{account} 만기 {eday}")
    except Exception as e:
        await update.message.reply_text(f"형식 오류: {e}")

async def cmd_list_lockup(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    rows = [r for r in read_lockups() if r["chat_id"]==str(update.effective_chat.id)]
    if not rows:
        await update.message.reply_text("등록된 확약이 없습니다.")
        return
    today = datetime.datetime.now(KST).date()
    lines=[]
    for r in rows:
        lines.append(f'{r["id"]}) {r["ticker"]}/{r["account"]} 만기 {r["lockup_end"]} (D{dday(r["lockup_end"], today):+d}) 수량 {r["quantity"]:,}')
    await update.message.reply_text("\n".join(lines))

def _smart_split_event_args(raw:str):
    # "ISSUER,EVENT_TYPE,DATE[,TIME][,OFFSETS][- NOTES]" 형태
    # NOTES에 콤마가 들어갈 수 있어 '-' 기준으로 분리
    note = ""
    if " - " in raw:
        main, note = raw.split(" - ", 1)
    elif " -" in raw:
        main, note = raw.split(" -", 1)
    else:
        main = raw
    parts = [p.strip() for p in main.split(",")]
    return parts, note.strip()

async def cmd_add_event(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    try:
        raw = " ".join(context.args)
        parts, note = _smart_split_event_args(raw)
        # 최소: issuer,event_type,event_date
        if len(parts) < 3:
            raise ValueError("필수값 부족 (ISSUER,EVENT_TYPE,YYYY-MM-DD)")
        issuer, event_type, edate = parts[0], parts[1], parts[2]
        etime = ""
        offsets = "0"
        if len(parts)>=4 and ":" in parts[3]:
            etime = parts[3]
            if len(parts)>=5: offsets = parts[4]
        elif len(parts)>=4:
            # 4번째가 offsets일 가능성
            offsets = parts[3]
        _id = add_event({
            "issuer": issuer,
            "event_type": event_type,
            "event_date": edate,
            "event_time": etime,
            "notes": note,
            "chat_id": str(update.effective_chat.id),
            "alert_offsets": offsets
        })
        await update.message.reply_text(f"[Event 등록] id={_id} / {issuer} {event_type} {edate} {(' '+etime) if etime else ''} offsets={offsets}")
    except Exception as e:
        await update.message.reply_text(f"형식 오류: {e}")

async def cmd_list_event(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    rows = [r for r in read_events() if r["chat_id"]==str(update.effective_chat.id)]
    if not rows:
        await update.message.reply_text("등록된 이벤트가 없습니다.")
        return
    lines=[]
    for r in rows:
        t = (f'{r["event_time"][0]:02d}:{r["event_time"][1]:02d}' if r["event_time"] else "09:00")
        offs = ",".join(str(x) for x in r["alert_offsets"])
        lines.append(f'{r["id"]}) {r["issuer"]} {r["event_type"]} {r["event_date"]} {t} offsets[{offs}]')
    await update.message.reply_text("\n".join(lines))

# ---------- Schedulers ----------
async def job_lockups(app:Application):
    today = datetime.datetime.now(KST).date()
    targets = {"D-30":30, "D-7":7, "D-1":1, "D-0":0}
    for r in read_lockups():
        dd = dday(r["lockup_end"], today)
        for stage, d in targets.items():
            if dd == d:
                key = today.strftime("%Y%m%d")
                if exists_lockup_log(r["id"], stage, key):
                    continue
                text = (
                    f"🔔 확약 만기 {stage}\n"
                    f"- 종목: {r['ticker']}\n"
                    f"- 계정: {r['account']}\n"
                    f"- 수량: {r['quantity']:,}\n"
                    f"- 확약시작: {r['lockup_start'].isoformat()}\n"
                    f"- 확약종료: {r['lockup_end'].isoformat()}\n"
                    f"- 비고: {r.get('notes','-')}"
                )
                await _send(app, int(r["chat_id"]), text)
                append_lockup_log(r["id"], stage, key)

def _fmt_hhmm(dt:datetime.datetime)->str:
    return f"{dt:%Y%m%d%H%M}"

async def job_events(app:Application):
    now = datetime.datetime.now(KST)
    today = now.date()
    hhmm_now = now.strftime("%H:%M")
    for e in read_events():
        # 오프셋별 발송 판단
        for off in e["alert_offsets"]:
            target_day = e["event_date"] + datetime.timedelta(days=off)
            # 발송 시간 결정: 당일(0) = event_time 또는 09:00 / 사전일 = 09:00
            if off == 0:
                hhmm_target = (f"{e['event_time'][0]:02d}:{e['event_time'][1]:02d}" if e["event_time"] else "09:00")
            else:
                hhmm_target = "09:00"
            if target_day == today and hhmm_now == hhmm_target:
                stage = "D-0" if off == 0 else f"D{off}"  # off=-7 -> 'D-7'
                key = _fmt_hhmm(now)
                if exists_event_log(e["id"], stage, key):
                    continue
                text = (
                    f"📅 이벤트 알림 {stage}\n"
                    f"- 발행사: {e['issuer']}\n"
                    f"- 유형: {e['event_type']}\n"
                    f"- 날짜/시간: {e['event_date'].isoformat()} {hhmm_target}\n"
                    f"- 비고: {e.get('notes','-')}"
                )
                await _send(app, int(e["chat_id"]), text)
                append_event_log(e["id"], stage, key)

def schedule(app:Application):
    sched = BackgroundScheduler(timezone="Asia/Seoul")
    # 확약: 매일 09:00
    sched.add_job(lambda: app.create_task(job_lockups(app)), trigger="cron", hour=9, minute=0)
    # 이벤트: 매분 체크(당일/오프셋 시간 일치 시 발송)
    sched.add_job(lambda: app.create_task(job_events(app)), trigger="cron", minute="*")
    sched.start()

def main():
    ensure_headers()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("add_lockup", cmd_add_lockup))
    app.add_handler(CommandHandler("list_lockup", cmd_list_lockup))
    app.add_handler(CommandHandler("add_event", cmd_add_event))
    app.add_handler(CommandHandler("list_event", cmd_list_event))
    schedule(app)
    app.run_polling()

if __name__ == "__main__":
    main()
