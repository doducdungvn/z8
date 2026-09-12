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
    retained = b.get_retained_bets()
    raw_messages = bot_service.get_all_raw_messages()
    pending = b.calculate_excess()
    pending_text = b.format_transfer_message(pending, include_header=False)
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
        }
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
    if not chat_id or history_idx is None:
        return jsonify({"success": False, "error": "Thiếu chat_id hoặc history_idx"}), 400
    
    ok = bot_service.delete_single_raw_message(chat_id, int(history_idx))
    return jsonify({"success": ok})



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
    date_arg = request.args.get("date") or (request.json or {}).get("date") if request.is_json else None
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
    if date_arg:
        kq = fetch_xsmb(date_arg)
    else:
        if not bot_service.cached_kqxs:
            bot_service.cached_kqxs = fetch_xsmb()
        kq = bot_service.cached_kqxs

    price_cfg = bot_service.config.get("price_config")
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

    kq = fetch_xsmb(date_arg)
    if not kq.get("success") or not kq.get("prizes"):
        return jsonify({"success": False, "error": f"Chưa có kết quả xổ số ngày {date_arg or 'hôm nay'} để chốt tiền."}), 400

    res = bot_service.settle_all(
        kq,
        notify_clients=send_tg,
        notify_recipient=send_tg,
        notify_owner=send_tg
    )
    return jsonify(res)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    print(f"[OK] Telegram Bot Backend Server running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
