import os
import sys
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from flask import Flask, request, jsonify

# Đảm bảo đường dẫn import
sys.path.append(os.path.dirname(__file__))

from telegram_service import bot_service
from lottery_engine import (
    fetch_xsmb,
    calculate_board_accounting,
    calculate_single_client_accounting,
    format_accounting_report
)

app = Flask(__name__)

# Cho phép CORS cho frontend localhost/lk và LAN IP
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = app.make_default_options_response()
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        res.headers["Access-Control-Allow-Headers"] = "*"
        res.headers["Access-Control-Allow-Private-Network"] = "true"
        return res

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


# Chỉ tự động kích hoạt bot khi có biến môi trường AUTO_START_BOT=true (ví dụ trên Cloud server).
# Trên localhost mặc định KHÔNG tự ý bật bot để tránh xung đột getUpdates và gửi nhầm tin.
if os.environ.get("AUTO_START_BOT", "").lower() in ["true", "1"]:
    if bot_service.config.get("bot_token") and not bot_service.is_running:
        bot_service.start()


@app.route("/api/bot/status", methods=["GET"])
def get_status():
    b_mode = bot_service.config.get("bot_mode", "auto")
    pending_cnt = len([b for b in getattr(bot_service, "pending_bets", []) if b.get("status") == "pending"])
    settle_st = bot_service.get_settle_status()
    raw_msgs = bot_service.get_all_raw_messages()
    return jsonify({
        "running": bot_service.is_running,
        "is_running": bot_service.is_running,
        "bot_mode": b_mode,
        "is_active": (b_mode == "auto"),
        "pending_count": pending_cnt,
        "total_messages": len(raw_msgs),
        "last_bet_timestamp": bot_service.last_bet_timestamp,
        "stats": bot_service.stats,
        "step_count": bot_service.balancer.step_count,
        "has_token": bool(bot_service.config.get("bot_token")),
        "recipient": bot_service.config.get("target_recipient"),
        "allowed_count": len(bot_service.config.get("allowed_senders", [])),
        "is_settled": settle_st.get("is_settled", False),
        "settled_date": settle_st.get("settled_date", ""),
        "settled_at": settle_st.get("settled_at", ""),
        "settled_time": settle_st.get("settled_time", ""),
        "settle_status": settle_st
    })


@app.route("/api/bot/config", methods=["GET"])
def get_config():
    cfg = dict(bot_service.config)
    cfg["has_admin_password"] = bool(cfg.get("admin_password"))
    cfg.pop("admin_password", None)
    return jsonify(cfg)


@app.route("/api/bot/verify_password", methods=["POST"])
def verify_password():
    data = request.json or {}
    pwd = str(data.get("password", "")).strip()
    actual = str(bot_service.config.get("admin_password", "123456")).strip()
    if pwd and pwd == actual:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Mật khẩu không chính xác!"}), 401


@app.route("/api/bot/change_password", methods=["POST"])
def change_password():
    data = request.json or {}
    old_pwd = str(data.get("old_password", "")).strip()
    new_pwd = str(data.get("new_password", "")).strip()
    actual = str(bot_service.config.get("admin_password", "123456")).strip()

    if old_pwd != actual:
        return jsonify({"success": False, "error": "Mật khẩu hiện tại không đúng!"}), 400
    if not new_pwd or len(new_pwd) < 4:
        return jsonify({"success": False, "error": "Mật khẩu mới phải có ít nhất 4 ký tự!"}), 400

    bot_service.config["admin_password"] = new_pwd
    bot_service.save_config()
    bot_service.log("Đã đổi mật khẩu quản trị hệ thống thành công", "SUCCESS")
    return jsonify({"success": True, "message": "Đã đổi mật khẩu quản trị thành công!"})


@app.route("/api/bot/config", methods=["POST"])
def update_config():
    data = request.json or {}
    current = bot_service.config

    if "bot_token" in data:
        current["bot_token"] = str(data["bot_token"]).strip()
    if "allowed_senders" in data:
        current["allowed_senders"] = data["allowed_senders"]
    if "target_recipient" in data:
        current["target_recipient"] = str(data["target_recipient"]).strip()
    if "auto_reply_client" in data:
        current["auto_reply_client"] = bool(data["auto_reply_client"])
    if "auto_forward_excess" in data:
        current["auto_forward_excess"] = bool(data["auto_forward_excess"])
    if "check_recipient_ack" in data:
        current["check_recipient_ack"] = bool(data["check_recipient_ack"])
    if "forward_contractor_to_owner" in data:
        current["forward_contractor_to_owner"] = bool(data["forward_contractor_to_owner"])
    if "forward_client_to_owner" in data:
        current["forward_client_to_owner"] = bool(data["forward_client_to_owner"])
    if "bet_filter_enabled" in data:
        current["bet_filter_enabled"] = bool(data["bet_filter_enabled"])
    if "bet_filter_keywords" in data:
        current["bet_filter_keywords"] = str(data["bet_filter_keywords"]).strip()
    if "cancel_detail_client" in data:
        current["cancel_detail_client"] = bool(data["cancel_detail_client"])
    if "cancel_detail_contractor" in data:
        current["cancel_detail_contractor"] = bool(data["cancel_detail_contractor"])
    if "ok_detail_client" in data:
        current["ok_detail_client"] = bool(data["ok_detail_client"])
    if "ok_detail_contractor" in data:
        current["ok_detail_contractor"] = bool(data["ok_detail_contractor"])
    if "mode" in data:
        current["mode"] = data["mode"]
    if "retain_config" in data:
        current["retain_config"] = data["retain_config"]
    if "price_config" in data:
        current["price_config"] = data["price_config"]
    if "client_prices" in data:
        current["client_prices"] = data["client_prices"]
    if "cleanup_after_hours" in data:
        try:
            h = float(data["cleanup_after_hours"])
            if h > 0:
                current["cleanup_after_hours"] = h
                current["cleanup_after_seconds"] = int(h * 3600)
        except Exception:
            pass
    elif "cleanup_after_seconds" in data:
        try:
            s = int(data["cleanup_after_seconds"])
            if s > 0:
                current["cleanup_after_seconds"] = s
                current["cleanup_after_hours"] = round(s / 3600.0, 2)
        except Exception:
            pass
    if "history_retention_hours" in data:
        try:
            h_val = float(data["history_retention_hours"])
            if h_val >= 0:
                current["history_retention_hours"] = h_val
                current["history_retention_days"] = round(h_val / 24.0, 2)
                if h_val > 0:
                    bot_service.cleanup_old_history(h_val)
        except Exception:
            pass
    elif "history_retention_days" in data:
        try:
            d_val = float(data["history_retention_days"])
            if d_val >= 0:
                current["history_retention_days"] = d_val
                current["history_retention_hours"] = round(d_val * 24.0, 1)
                if d_val > 0:
                    bot_service.cleanup_old_history(d_val * 24.0)
        except Exception:
            pass
    if "owner_chat_id" in data:
        current["owner_chat_id"] = str(data["owner_chat_id"]).strip()
    if "admin_password" in data and str(data["admin_password"]).strip():
        current["admin_password"] = str(data["admin_password"]).strip()
    if "authenticated_admins" in data:
        current["authenticated_admins"] = data["authenticated_admins"]
    if "auto_fetch_kqxs_daily" in data:
        current["auto_fetch_kqxs_daily"] = bool(data["auto_fetch_kqxs_daily"])

    bot_service.save_config(current)
    safe_cfg = dict(bot_service.config)
    safe_cfg["has_admin_password"] = bool(safe_cfg.get("admin_password"))
    safe_cfg.pop("admin_password", None)
    return jsonify({"success": True, "config": safe_cfg})


@app.route("/api/bot/cleanup_history", methods=["POST"])
def cleanup_history():
    data = request.json or {}
    hours = data.get("hours")
    if hours is not None:
        try:
            hours = float(hours)
        except Exception:
            hours = None
    elif "days" in data and data.get("days") is not None:
        try:
            hours = float(data["days"]) * 24.0
        except Exception:
            hours = None
    deleted = bot_service.cleanup_old_history(hours)
    return jsonify({
        "success": True,
        "deleted_files_count": deleted,
        "message": f"Đã dọn dẹp {deleted} file lịch sử cũ thành công."
    })


@app.route("/api/bot/start", methods=["POST"])
def start_bot():
    res = bot_service.start()
    return jsonify(res)


@app.route("/api/bot/stop", methods=["POST"])
def stop_bot():
    res = bot_service.stop()
    return jsonify(res)


@app.route("/api/bot/stop_polling", methods=["POST"])
def stop_polling_route():
    res = bot_service.stop_polling()
    return jsonify(res)


@app.route("/api/bot/toggle_mode", methods=["POST"])
def toggle_mode_route():
    cur = bot_service.config.get("bot_mode", "auto")
    new_mode = "manual" if cur == "auto" else "auto"
    if new_mode == "auto":
        res = bot_service.start()
    else:
        res = bot_service.stop()
    return jsonify(res)


@app.route("/api/bot/pending_bets", methods=["GET"])
def get_pending_bets_route():
    pb = getattr(bot_service, "pending_bets", [])
    return jsonify({
        "success": True,
        "pending_bets": pb,
        "pending_count": len([b for b in pb if b.get("status") == "pending"])
    })


@app.route("/api/bot/approve_bet", methods=["POST"])
def approve_bet_route():
    data = request.json or {}
    pid = data.get("pending_id")
    if pid is None:
        return jsonify({"success": False, "error": "Thiếu mã tin cược pending_id"}), 400
    res = bot_service.approve_pending_bet(int(pid))
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/bot/approve_all_bets", methods=["POST"])
def approve_all_bets_route():
    res = bot_service.approve_all_pending_bets()
    return jsonify(res)


@app.route("/api/bot/reject_bet", methods=["POST"])
def reject_bet_route():
    data = request.json or {}
    pid = data.get("pending_id")
    notify = bool(data.get("notify_client", False))
    reason = str(data.get("reason", "")).strip()
    if pid is None:
        return jsonify({"success": False, "error": "Thiếu mã tin cược pending_id"}), 400
    res = bot_service.reject_pending_bet(int(pid), notify_client=notify, reason=reason)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/bot/test_message", methods=["POST"])
def test_message():
    data = request.json or {}
    recipient = data.get("recipient") or bot_service.config.get("target_recipient")
    text = data.get("text", "🔔 <b>Kiểm tra kết nối Bot Telegram thành công!</b>\nHệ thống cân cược tự động đã sẵn sàng.")

    if not recipient:
        return jsonify({"success": False, "error": "Chưa có ID người nhận (Chat ID)"}), 400

    ok, err = bot_service.send_telegram_message(recipient, text)
    if ok:
        bot_service.log(f"Gửi tin nhắn test tới {recipient} thành công.", "SUCCESS")
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": err}), 400


@app.route("/api/bot/known_users", methods=["GET"])
def get_known_users():
    bot_service.known_users = bot_service.load_known_users()
    return jsonify(bot_service.known_users)


@app.route("/api/bot/known_users/<user_id>", methods=["DELETE", "POST"])
def delete_known_user(user_id):
    user_id = str(user_id).strip()
    bot_service.known_users = bot_service.load_known_users()
    deleted = False
    deleted_username = None

    # 1. Tìm và xóa khỏi known_users
    if user_id in bot_service.known_users:
        u_info = bot_service.known_users.pop(user_id)
        deleted_username = u_info.get("username")
        deleted = True
    else:
        # Tra cứu theo username nếu truyền vào username
        target_uid = None
        norm_id = user_id.lstrip("@").lower()
        for uid, info in bot_service.known_users.items():
            if (info.get("username") or "").lower() == norm_id:
                target_uid = uid
                deleted_username = info.get("username")
                break
        if target_uid:
            bot_service.known_users.pop(target_uid)
            deleted = True

    if deleted:
        bot_service.save_known_users()

        # 2. Xóa khỏi client_prices nếu có
        cfg = bot_service.config
        prices = cfg.get("client_prices", {})
        keys_to_remove = [k for k in prices if k == user_id or (deleted_username and k in [deleted_username, f"@{deleted_username}"])]
        for k in keys_to_remove:
            prices.pop(k, None)

        # 3. Đồng thời gỡ khỏi allowed_senders nếu có
        raw_allowed = cfg.get("allowed_senders", [])
        if isinstance(raw_allowed, str):
            raw_allowed = [x.strip() for x in raw_allowed.split(",") if x.strip()]
        if isinstance(raw_allowed, list):
            norm_targets = [user_id.lower()]
            if deleted_username:
                norm_targets.extend([deleted_username.lower(), f"@{deleted_username.lower()}"])
            new_allowed = [x for x in raw_allowed if x.lower() not in norm_targets]
            if len(new_allowed) != len(raw_allowed):
                cfg["allowed_senders"] = new_allowed

        bot_service.save_config()
        bot_service.log(f"Đã xóa người dùng {user_id} khỏi danh bạ CRM.", "INFO")
        return jsonify({"success": True, "message": f"Đã xóa người dùng {user_id}"})
    else:
        return jsonify({"success": False, "error": "Không tìm thấy người dùng trong danh bạ"}), 404


@app.route("/api/bot/logs", methods=["GET"])
def get_logs():
    return jsonify({
        "logs": bot_service.logs[-100:]  # 100 log mới nhất
    })


@app.route("/api/bot/clear_logs", methods=["POST"])
def clear_logs():
    bot_service.logs.clear()
    return jsonify({"success": True, "message": "Đã xóa toàn bộ nhật ký"})


@app.route("/api/bot/history_logs", methods=["GET"])
def get_history_logs():
    date_str = request.args.get("date", "").strip()
    log_path = os.path.join(os.path.dirname(__file__), "bot_activity.log")
    if not os.path.exists(log_path):
        return jsonify({"logs": []})
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if date_str:
            lines = [l for l in lines if date_str in l]
        return jsonify({"logs": lines[-300:]})
    except Exception as e:
        return jsonify({"logs": [], "error": str(e)})


@app.route("/api/bot/board", methods=["GET"])
def get_board():
    date_arg = request.args.get("date")
    today_str = getattr(bot_service, "current_date", "") or datetime.now().strftime("%Y-%m-%d")
    clean_date = date_arg.replace("/", "-").strip() if date_arg else ""
    parts = clean_date.split("-")
    if len(parts) == 3 and len(parts[0]) == 2 and len(parts[2]) == 4:
        clean_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

    is_today = not clean_date or (clean_date == today_str)

    if not is_today:
        archive = bot_service.load_daily_archive(date_arg)
        if archive:
            b_data = archive.get("board", {})
            return jsonify({
                "from_archive": True,
                "date": archive.get("date"),
                "display_date": archive.get("display_date", date_arg),
                "saved_at": archive.get("saved_at"),
                "step_count": len(archive.get("transfer_history", [])),
                "de_sums": b_data.get("de_sums", {}),
                "lo_sums": b_data.get("lo_sums", {}),
                "bacang_sums": b_data.get("bacang_sums", {}),
                "xien_count": len(b_data.get("xien_bets", [])),
                "history": archive.get("transfer_history", []),
                "retained": b_data.get("retained", {}),
                "raw_messages": archive.get("raw_messages", []),
                "pending_transfers": {},
                "pending_text": "",
                "transfers": {},
                "is_settled": True,
                "settled_date": archive.get("display_date", date_arg),
                "settled_at": archive.get("saved_at", ""),
                "settled_time": archive.get("saved_at", "").split(" ")[0] if " " in archive.get("saved_at", "") else "",
                "settle_status": {
                    "is_settled": True,
                    "settled_date": archive.get("display_date", date_arg),
                    "settled_at": archive.get("saved_at", "")
                },
                "cached_kqxs": archive.get("kqxs")
            })

    # Nếu xem bảng ngày hôm nay, tự động kiểm tra xem đã qua nửa đêm sang ngày mới chưa
    bot_service.check_and_rollover_date()

    b = bot_service.balancer
    retained = b.get_retained_bets()
    raw_messages = bot_service.get_all_raw_messages()
    pending = b.calculate_excess()
    pending_text = b.format_transfer_message(pending, include_header=False)
    settle_st = bot_service.get_settle_status()
    return jsonify({
        "step_count": b.step_count,
        "de_sums": b.de_sums,
        "lo_sums": b.lo_sums,
        "bacang_sums": b.bacang_sums,
        "xien_count": len(b.xien_bets),
        "history": b.transfer_history,
        "retained": retained,
        "raw_messages": raw_messages,
        "pending_transfers": pending,
        "pending_text": pending_text,
        "transfers": {
            "de": b.cumulative_de_transfers,
            "lo": b.cumulative_lo_transfers,
            "bacang": b.cumulative_bacang_transfers,
            "xien": b.cumulative_xien_transfers
        },
        "is_settled": settle_st.get("is_settled", False),
        "settled_date": settle_st.get("settled_date", ""),
        "settled_at": settle_st.get("settled_at", ""),
        "settled_time": settle_st.get("settled_time", ""),
        "settle_status": settle_st,
        "cached_kqxs": bot_service.cached_kqxs
    })


@app.route("/api/bot/sync_web_bets", methods=["POST"])
def sync_web_bets():
    data = request.json or {}
    bet_text = str(data.get("bet_text", "")).strip()
    force = bool(data.get("force", False))
    if not bet_text:
        return jsonify({"success": False, "error": "Chưa có nội dung cược để đồng bộ."}), 400

    res = bot_service.add_web_bets(bet_text, force=force)
    if res.get("is_duplicate"):
        return jsonify(res), 200
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/bot/reload_shorthands", methods=["POST"])
def reload_shorthands_route():
    from bet_parser import reload_shorthands
    count = reload_shorthands()
    bot_service.log(f"Đã nạp lại {count} từ viết tắt từ nhaptat.txt", "SUCCESS")
    return jsonify({"success": True, "count": count})



@app.route("/api/bot/reset_board", methods=["POST"])
def reset_board():
    bot_service.balancer.reset_board()
    bot_service.last_web_bet_hash = None
    bot_service.last_web_bet_text = ""
    bot_service.client_bets = {}
    bot_service.save_client_bets()
    bot_service.client_msg_counters = {}
    bot_service.log("Đã làm mới (reset) bảng cược và xóa toàn bộ tin nhắn gốc về 0.", "INFO")
    return jsonify({"success": True})


@app.route("/api/bot/delete_message", methods=["POST"])
def delete_message():
    data = request.json or {}
    chat_id = str(data.get("chat_id", "")).strip()
    history_idx = data.get("history_idx")
    pending_id = data.get("pending_id")
    raw_text = data.get("raw_text")

    clean_pending_id = None
    if pending_id not in [None, "", "null", "undefined"]:
        try:
            clean_pending_id = int(pending_id)
        except (ValueError, TypeError):
            clean_pending_id = None

    clean_h_idx = None
    if history_idx not in [None, "", "null", "undefined"]:
        try:
            clean_h_idx = int(history_idx)
        except (ValueError, TypeError):
            clean_h_idx = None

    if clean_pending_id is None and not chat_id and clean_h_idx is None and not raw_text:
        return jsonify({"success": False, "error": "Thiếu thông tin nhận diện tin nhắn cần xóa"}), 400

    ok = bot_service.delete_single_raw_message(chat_id, clean_h_idx, pending_id=clean_pending_id, raw_text=raw_text)
    return jsonify({"success": ok, "message": "Đã xóa tin nhắn thành công" if ok else "Không tìm thấy tin nhắn cần xóa"})


@app.route("/api/bot/void_message", methods=["POST"])
def void_message():
    """Bỏ qua (hủy) hoặc khôi phục tin nhắn cược của khách hoặc tin chuyển của chủ thầu"""
    data = request.json or {}
    msg_type = data.get("type", "client")
    if msg_type == "client":
        chat_id = str(data.get("chat_id", "")).strip()
        history_idx = data.get("history_idx")
        if not chat_id or history_idx is None:
            return jsonify({"success": False, "error": "Thiếu chat_id hoặc history_idx"}), 400
        res = bot_service.toggle_client_message_void(chat_id, int(history_idx))
        return jsonify(res)
    elif msg_type == "transfer":
        step_idx = data.get("step_idx")
        if step_idx is None:
            return jsonify({"success": False, "error": "Thiếu step_idx"}), 400
        res = bot_service.toggle_transfer_void(int(step_idx))
        return jsonify(res)
    return jsonify({"success": False, "error": "Loại tin nhắn không hợp lệ"}), 400



@app.route("/api/bot/manual_transfer", methods=["POST"])
def manual_transfer():
    excess = bot_service.balancer.calculate_excess()
    excess_count = sum(len(v) for v in excess.values())
    if excess_count == 0:
        return jsonify({"success": False, "message": "Không có cược nào vượt hạn mức giữ lại để chuyển."})

    recipient = bot_service.config.get("target_recipient")
    if not recipient:
        return jsonify({"success": False, "error": "Chưa thiết lập Chat ID người nhận cược thừa."}), 400

    transfer_msg = bot_service.balancer.format_transfer_message(excess, header_prefix="Thầu Chuyển")
    ok, err = bot_service.send_telegram_message(recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
    if ok:
        bot_service.balancer.commit_transfers(excess)
        bot_service.stats["transfers_sent"] += 1
        bot_service.log(f"Bắn cược thừa thủ công tới {recipient}:\n{transfer_msg}", "SUCCESS")
        return jsonify({"success": True, "message": transfer_msg})
    else:
        return jsonify({"success": False, "error": err}), 500


@app.route("/api/bot/cleanup", methods=["GET", "POST"])
def manage_cleanup():
    """Kiểm tra và kích hoạt dọn dẹp xóa dấu vết cược sau 24h"""
    if request.method == "POST":
        data = request.json or {}
        force = data.get("force", False)
        deleted, remaining = bot_service.check_and_cleanup_traces(force=force)
        return jsonify({"success": True, "deleted_count": deleted, "remaining_count": remaining})
    return jsonify({
        "tracked_messages_count": len(bot_service.tracked_messages),
        "cleanup_after_seconds": bot_service.config.get("cleanup_after_seconds", 86400),
        "tracked_sample": bot_service.tracked_messages[-20:]
    })


@app.route("/api/bot/kqxs", methods=["GET", "POST"])
def get_kqxs():
    date_arg = request.args.get("date")
    if not date_arg and request.is_json and request.json:
        date_arg = request.json.get("date")
    force = request.args.get("force") == "true" or request.method == "POST"

    if date_arg:
        kq = fetch_xsmb(date_arg)
        return jsonify(kq)

    if force or not bot_service.cached_kqxs:
        bot_service.cached_kqxs = fetch_xsmb()
    return jsonify(bot_service.cached_kqxs)


@app.route("/api/bot/report", methods=["GET"])
def get_report():
    date_arg = request.args.get("date")
    today_slash = datetime.now().strftime("%d/%m/%Y")
    target_date = date_arg or today_slash

    if date_arg:
        archive = bot_service.load_daily_archive(date_arg)
        if archive:
            settle_res = archive.get("settle_result", {})
            acc = settle_res.get("accounting", {})
            thau_txt = settle_res.get("thau_report")
            chuyen_txt = settle_res.get("chuyen_report")
            giulai_txt = settle_res.get("giulai_report")
            return jsonify({
                "from_archive": True,
                "date": archive.get("date"),
                "display_date": archive.get("display_date", date_arg),
                "saved_at": archive.get("saved_at"),
                "accounting": acc,
                "text_thau": thau_txt or format_accounting_report(acc, "thau"),
                "text_chuyen": chuyen_txt or format_accounting_report(acc, "chuyen"),
                "text_giulai": giulai_txt or format_accounting_report(acc, "giulai"),
                "summary_text": settle_res.get("summary_text", ""),
                "clients": settle_res.get("client_messages", []),
                "kqxs": archive.get("kqxs", {})
            })
        kq = fetch_xsmb(date_arg)
    else:
        if not bot_service.cached_kqxs or bot_service.cached_kqxs.get("date") != today_slash:
            bot_service.cached_kqxs = fetch_xsmb(today_slash)
        kq = bot_service.cached_kqxs

    price_cfg = bot_service.config.get("price_config")

    # KIỂM TRA BẮT BUỘC: Nếu chưa có KQXS hoặc KQXS không khớp ngày cược, KHÔNG tính ăn thua bằng kết quả cũ
    if not kq.get("success") or not kq.get("is_complete") or (kq.get("date") and kq.get("date") != target_date):
        kq_empty = {
            "success": False,
            "date": target_date,
            "error": kq.get("error") or f"Chưa có kết quả xổ số ngày {target_date}",
            "special_prize": "",
            "special_last2": "",
            "special_last3": "",
            "prizes": [],
            "all_last2": [],
            "is_complete": False
        }
        acc = calculate_board_accounting(bot_service.balancer, kq_empty, price_cfg)
        return jsonify({
            "accounting": acc,
            "text_thau": format_accounting_report(acc, "thau"),
            "text_chuyen": format_accounting_report(acc, "chuyen"),
            "text_giulai": format_accounting_report(acc, "giulai"),
            "clients": [],
            "kqxs": kq,
            "no_valid_kqxs": True,
            "error_kqxs": kq.get("error") or f"Chưa có KQXS ngày {target_date}. Không được dùng kết quả ngày cũ để tính toán."
        })

    acc = calculate_board_accounting(bot_service.balancer, kq, price_cfg)

    # Chi tiết từng khách cược (áp dụng giá riêng từng người nếu có)
    custom_client_prices = bot_service.config.get("client_prices", {})
    clients_summary = []
    for cid_str, cdata in bot_service.client_bets.items():
        c_price_cfg = bot_service.get_client_price_config(cid_str, cdata.get("username"))
        c_res = calculate_single_client_accounting(cdata, kq, c_price_cfg)
        has_custom_price = (cid_str in custom_client_prices) or (cdata.get("username") and f"@{cdata.get('username')}" in custom_client_prices) or (cdata.get("username") in custom_client_prices)
        clients_summary.append({
            "chat_id": cid_str,
            "name": cdata.get("name"),
            "username": cdata.get("username"),
            "msg_count": cdata.get("msg_count", 0),
            "has_custom_price": bool(has_custom_price),
            "custom_price": c_price_cfg if has_custom_price else None,
            "accounting": c_res["accounting"],
            "report_text": c_res["report_text"],
            "last_settled": cdata.get("last_settled")
        })

    return jsonify({
        "accounting": acc,
        "text_thau": format_accounting_report(acc, "thau"),
        "text_chuyen": format_accounting_report(acc, "chuyen"),
        "text_giulai": format_accounting_report(acc, "giulai"),
        "clients": clients_summary,
        "kqxs": kq
    })


@app.route("/api/bot/client_prices", methods=["GET", "POST", "DELETE"])
def manage_client_prices():
    prices = bot_service.config.get("client_prices", {})
    if request.method == "GET":
        return jsonify({"success": True, "client_prices": prices})

    data = request.json or {}
    client_key = str(data.get("client_key") or data.get("chat_id") or data.get("username") or "").strip()
    if not client_key:
        return jsonify({"success": False, "error": "Chưa chỉ định ID hoặc Username khách cược"}), 400

    if request.method == "DELETE" or data.get("action") == "delete":
        if client_key in prices:
            del prices[client_key]
        alt_key = client_key.lstrip("@")
        if alt_key in prices:
            del prices[alt_key]
        if f"@{alt_key}" in prices:
            del prices[f"@{alt_key}"]
        bot_service.config["client_prices"] = prices
        bot_service.save_config()
        bot_service.log(f"Đã xóa cấu hình giá riêng của khách {client_key}", "INFO")
        return jsonify({"success": True, "client_prices": prices})

    # POST: Update/Set custom price
    rate_cfg = data.get("rates") or data.get("price_config") or {}
    if not rate_cfg:
        return jsonify({"success": False, "error": "Chưa có thông số bảng giá"}), 400

    prices[client_key] = rate_cfg
    bot_service.config["client_prices"] = prices
    bot_service.save_config()
    bot_service.log(f"Đã cập nhật bảng giá riêng cho khách {client_key}", "SUCCESS")
    return jsonify({"success": True, "client_prices": prices})


@app.route("/api/bot/client_bets", methods=["GET"])
def get_client_bets():
    return jsonify(bot_service.client_bets)


@app.route("/api/bot/settle_now", methods=["POST"])
def settle_now():
    data = request.json or {}
    date_arg = data.get("date")
    send_tg = data.get("send_telegram", True)

    today_slash = datetime.now().strftime("%d/%m/%Y")
    target_date = (date_arg or today_slash).strip()

    kq = fetch_xsmb(target_date)
    # KIỂM TRA NGHIÊM NGẶT: Bắt buộc KQXS phải thành công, đủ 27 giải và KHỚP ĐÚNG NGÀY
    if not kq.get("success") or not kq.get("is_complete") or not kq.get("prizes"):
        err_msg = kq.get("error") or f"Chưa có kết quả xổ số ngày {target_date} để chốt tiền."
        return jsonify({
            "success": False,
            "error": err_msg,
            "detail": "Tuyệt đối không được phép lấy kết quả xổ số ngày hôm trước để tính toán chốt tiền!"
        }), 400

    # Đối chiếu ngày khớp chính xác (chuẩn hóa dấu /)
    kq_date = (kq.get("date") or "").replace("-", "/")
    target_clean = target_date.replace("-", "/")
    if kq_date != target_clean:
        return jsonify({
            "success": False,
            "error": f"Ngày kết quả xổ số ({kq_date}) không khớp với ngày cược ({target_clean}). Không được dùng kết quả ngày cũ để chốt tiền!",
            "detail": "Bắt buộc phải lấy kết quả đúng ngày khớp với ngày nhắn tin cược và thầu."
        }), 400

    res = bot_service.settle_all(
        kq,
        notify_clients=send_tg,
        notify_recipient=send_tg,
        notify_owner=send_tg
    )
    bot_service._reset_after_settle(kq.get('date', ''), settle_result=res, kqxs=kq)
    return jsonify(res)



if __name__ == "__main__":
    if bot_service.config.get("bot_token") and bot_service.config.get("bot_mode", "auto") == "auto":
        try:
            bot_service.start()
        except Exception as e:
            print(f"[WARN] Khởi động bot tự động: {e}")
    port = int(os.environ.get("PORT", 5005))
    print(f"[OK] Telegram Bot Backend Server running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
