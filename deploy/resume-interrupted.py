#!/usr/bin/env python3
"""Review/prepare deployment continuations while valeria-web.service is stopped.

Run with PYTHONPATH=src; snapshot is a JSON list of pre-stop queue_jobs rows.
No upstream prompt is sent here. The regular dispatcher submits a prepared
continuation after startup using its durable message ID and original session.
"""

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path

from opencode_web.consultations import final_answer
from opencode_web.database import Database
from opencode_web.dispatcher import QueueDispatcher
from opencode_web.task_results import task_final_answer

CONTINUATION = (
    "Работа была прервана техническим перезапуском Valeria. Продолжи исходное задание "
    "в этой же сессии с места остановки, используя сохранённую историю. Сначала проверь "
    "результаты последних инструментов и фактическое состояние уже начатых действий. "
    "Не повторяй выполненные изменения, комментарии, создание задач, отправку сообщений "
    "или запуск пайплайнов. Если результат действия неизвестен, сначала проверь его "
    "через чтение; не повторяй запись вслепую. Не расширяй исходные полномочия и объём "
    "задания. Если всё уже выполнено, дай итоговый ответ. Сохраняй текущий режим работы: "
    "в фазе планирования закончи план, в консультации дай ответ, в фазе выполнения "
    "продолжи выполнение. Не описывай пользователю внутренние операции с памятью "
    "и не раскрывай идентификаторы записей памяти."
)


def history(connection, session_id):
    messages = []
    for row in connection.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id",
        (session_id,),
    ):
        info = json.loads(row["data"])
        info["id"] = row["id"]
        parts = [json.loads(part[0]) for part in connection.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id", (row["id"],)
        )]
        messages.append({"info": info, "parts": parts})
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/opencode-web.db"))
    parser.add_argument("--opencode-db", type=Path,
                        default=Path("/root/.local/share/opencode/opencode.db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    state = subprocess.check_output(
        ["systemctl", "show", "valeria-web.service", "-p", "ActiveState", "--value"], text=True
    ).strip()
    if state not in {"inactive", "failed"}:
        raise SystemExit("Stop valeria-web.service before reviewing/preparing continuations")
    upstream = sqlite3.connect(f"file:{args.opencode_db}?mode=ro", uri=True)
    upstream.row_factory = sqlite3.Row
    upstream.execute("PRAGMA query_only=ON")
    db = Database(args.database, "unused-offline-recovery")
    unresolved = False
    try:
        if args.apply:
            db.initialize()
        for previous in json.loads(args.snapshot.read_text()):
            if previous["status"] not in {"running", "waiting_permission", "dispatching"}:
                continue
            job = db.get_job(previous["id"])
            if not job:
                raise RuntimeError("Snapshot job disappeared")
            outcome = {"id": job["id"], "status": job["status"]}
            if job["status"] not in {"running", "waiting_permission"}:
                outcome["action"] = "leave_unchanged"
                print(json.dumps(outcome))
                continue
            mid = job.get("phase2_message_id") or job["message_id"]
            expected = previous.get("phase2_message_id") or previous["message_id"]
            if mid != expected or job["session_id"] != previous["session_id"]:
                raise RuntimeError("Run phase/session changed since snapshot; review again")
            messages = history(upstream, job["session_id"])
            found, result = QueueDispatcher._message_result(
                messages, mid, task_session=bool(job.get("task_id"))
            )
            answer = final_answer(messages, mid) if job.get("consultation") else None
            tools = [
                {"tool": part.get("tool"), "status": (part.get("state") or {}).get("status")}
                for message in messages[-3:] for part in message.get("parts", [])
                if part.get("type") == "tool"
            ]
            outcome["last_tools"] = tools
            if result == "completed" or answer:
                if job.get("plan_first") and not job.get("phase2_message_id"):
                    outcome["action"] = "recover_build_phase_normally"
                else:
                    outcome["action"] = "complete_without_resending"
                    if args.apply:
                        if job.get("task_id"):
                            db.complete_task_run(job["id"], task_final_answer(messages, mid))
                        elif job.get("consultation") and answer:
                            with db.transaction():
                                db.save_consultation_answer(job["id"], answer)
                                db.set_job_status(job["id"], "completed")
                        elif not job.get("consultation"):
                            db.set_job_status(job["id"], "completed")
                        else:
                            raise RuntimeError("Completed consultation has no final answer")
            elif found:
                outcome["action"] = "continue_in_original_session"
                if args.apply and not db.resume_interrupted_job(job["id"], mid, CONTINUATION):
                    raise RuntimeError("Continuation reservation failed")
            else:
                outcome["action"] = "manual_review_required_missing_prompt"
                unresolved = True
            print(json.dumps(outcome))
    finally:
        db.close()
        upstream.close()
    if unresolved:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
