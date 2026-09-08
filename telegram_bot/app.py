import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from flask import Flask, request, jsonify

# Đảm bảo đường dẫn import
sys.path.append(os.path.dirname(__file__))

from telegram_service import bot_service
from lottery_engine import fetch_xsmb, calculate_board_accounting, format_accounting_report

app = Flask(__name__)

# Cho phép CORS cho frontend localhost/lk
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# Tự động kích hoạt bot polling khi khởi chạy nếu đã cấu hình token
if bot_service.config.get("bot_token") and not bot_service.is_running:
    bot_service.start()


@app.route("/api/bot/status", methods=["GET"])
def get_status():
    return jsonify({
        "running": bot_service.is_running,
        "is_running": bot_service.is_running,
        "stats": bot_service.stats,
        "step_count": bot_service.balancer.step_count,
        "has_token": bool(bot_service.config.get("bot_token")),
        "recipient": bot_service.config.get("target_recipient"),
        "allowed_count": len(bot_service.config.get("allowed_senders", []))
    })


@app.route("/api/bot/config", methods=["GET"])
def get_config():
    return jsonify(bot_service.config)


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
    if "mode" in data:
        current["mode"] = data["mode"]
    if "retain_config" in data:
        current["retain_config"] = data["retain_config"]
    if "price_config" in data:
        current["price_config"] = data["price_config"]
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
    if "owner_chat_id" in data:
        current["owner_chat_id"] = str(data["owner_chat_id"]).strip()
    if "admin_password" in data:
        current["admin_password"] = str(data["admin_password"]).strip()
    if "authenticated_admins" in data:
        current["authenticated_admins"] = data["authenticated_admins"]
    if "auto_fetch_kqxs_daily" in data:
        current["auto_fetch_kqxs_daily"] = bool(data["auto_fetch_kqxs_daily"])

    bot_service.save_config(current)
    return jsonify({"success": True, "config": bot_service.config})


@app.route("/api/bot/start", methods=["POST"])
def start_bot():
    res = bot_service.start()
    return jsonify(res)


@app.route("/api/bot/stop", methods=["POST"])
def stop_bot():
    res = bot_service.stop()
    return jsonify(res)


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


@app.route("/api/bot/logs", methods=["GET"])
def get_logs():
    return jsonify({
        "logs": bot_service.logs[-100:]  # 100 log mới nhất
    })


@app.route("/api/bot/board", methods=["GET"])
def get_board():
    b = bot_service.balancer
    return jsonify({
        "step_count": b.step_count,
        "de_sums": b.de_sums,
        "lo_sums": b.lo_sums,
        "bacang_sums": b.bacang_sums,
        "xien_count": len(b.xien_bets),
        "history": b.transfer_history
    })


@app.route("/api/bot/reset_board", methods=["POST"])
def reset_board():
    bot_service.balancer.reset_board()
    bot_service.log("Đã làm mới (reset) bảng cược về 0.", "INFO")
    return jsonify({"success": True})


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
    force = request.args.get("force") == "true" or request.method == "POST"
    if force or not bot_service.cached_kqxs:
        bot_service.cached_kqxs = fetch_xsmb()
    return jsonify(bot_service.cached_kqxs)


@app.route("/api/bot/report", methods=["GET"])
def get_report():
    if not bot_service.cached_kqxs:
        bot_service.cached_kqxs = fetch_xsmb()
    
    acc = calculate_board_accounting(bot_service.balancer, bot_service.cached_kqxs, bot_service.config.get("price_config"))
    return jsonify({
        "accounting": acc,
        "text_thau": format_accounting_report(acc, "thau"),
        "text_chuyen": format_accounting_report(acc, "chuyen"),
        "text_giulai": format_accounting_report(acc, "giulai"),
        "kqxs": bot_service.cached_kqxs
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    print(f"[OK] Telegram Bot Backend Server running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
