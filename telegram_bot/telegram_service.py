import os
import sys
import json
import time
import threading
import requests
from datetime import datetime

from bet_parser import parse_bet_message, format_ok_receipt
from balancer import BoardBalancer
from lottery_engine import (
    DEFAULT_PRICE_CONFIG,
    fetch_xsmb,
    format_xsmb_message,
    calculate_board_accounting,
    format_accounting_report,
    format_price_config_summary
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "bot_activity.log")
KNOWN_USERS_PATH = os.path.join(os.path.dirname(__file__), "known_users.json")
TRACKED_MESSAGES_PATH = os.path.join(os.path.dirname(__file__), "tracked_messages.json")


class TelegramBotService:
    def __init__(self):
        self.config = self.load_config()
        self.known_users = self.load_known_users()
        self.tracked_messages = self.load_tracked_messages()
        self.last_cleanup_ts = 0
        self.last_bet_timestamp = None
        self.balancer = BoardBalancer(self.config.get("retain_config", {}))
        self.is_running = False
        self.polling_thread = None
        self.last_update_id = 0
        self.last_daily_report_date = None
        self.cached_kqxs = None
        self.logs = []  # Ring buffer log messages (max 200)
        self.client_msg_counters = {}  # Đếm số thứ tự tin của từng khách trong ngày (Ok 1, Ok 2...)
        self.stats = {
            "messages_received": 0,
            "transfers_sent": 0,
            "last_active": None,
            "bot_info": None,
            "error_message": None
        }
        if self.config.get("bot_token"):
            try:
                self.check_bot_token()
            except Exception:
                pass

    def load_config(self) -> dict:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    # Bổ sung các trường mới nếu thiếu
                    if "price_config" not in cfg:
                        cfg["price_config"] = DEFAULT_PRICE_CONFIG
                    if "owner_chat_id" not in cfg:
                        cfg["owner_chat_id"] = ""
                    if "cleanup_after_seconds" not in cfg:
                        cfg["cleanup_after_seconds"] = 86400  # 24 giờ tự động xóa vết cược
                    # Đọc bổ sung từ biến môi trường (nếu có, tiện cho Cloud hosting)
                    if os.environ.get("TELEGRAM_BOT_TOKEN") and not cfg.get("bot_token"):
                        cfg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN")
                    if os.environ.get("TARGET_RECIPIENT") and not cfg.get("target_recipient"):
                        cfg["target_recipient"] = os.environ.get("TARGET_RECIPIENT")
                    if os.environ.get("OWNER_CHAT_ID") and not cfg.get("owner_chat_id"):
                        cfg["owner_chat_id"] = os.environ.get("OWNER_CHAT_ID")
                    return cfg
            except Exception as e:
                self.log(f"Lỗi đọc config.json: {e}", "WARN")
        
        # Cấu hình mặc định
        default_cfg = {
            "bot_token": "",
            "allowed_senders": ["*"],     # Mặc định * để nhận cược thuận tiện
            "target_recipient": "",       # Telegram Chat ID của người nhận cược thừa (thầu trên)
            "owner_chat_id": "",          # Chat ID của chủ bảng (nhận báo cáo & lệnh admin)
            "auto_reply_client": True,    # Tự động báo nhận cược cho khách
            "auto_forward_excess": True,  # Tự động bắn cược thừa cho người nhận
            "auto_fetch_kqxs_daily": True,# Tự động lấy KQXS lúc 18h30
            "mode": "instant",            # "instant" hoặc "batch"
            "retain_config": BoardBalancer.default_config(),
            "price_config": DEFAULT_PRICE_CONFIG,
            "cleanup_after_seconds": 86400
        }
        self.save_config(default_cfg)
        return default_cfg

    def save_config(self, cfg: dict = None):
        if cfg:
            self.config = cfg
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            if hasattr(self, "balancer"):
                self.balancer.config = self.config.get("retain_config", {})
        except Exception as e:
            self.log(f"Lỗi lưu config.json: {e}", "ERROR")

    def load_known_users(self) -> dict:
        if os.path.exists(KNOWN_USERS_PATH):
            try:
                with open(KNOWN_USERS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_known_users(self):
        try:
            with open(KNOWN_USERS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.known_users, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_user(self, from_user: dict, chat_id: int):
        if not from_user:
            return
        uid = str(from_user.get("id"))
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        self.known_users[uid] = {
            "user_id": uid,
            "chat_id": chat_id,
            "username": username,
            "first_name": first_name,
            "updated_at": datetime.now().strftime("%H:%M:%S %d/%m")
        }
        self.save_known_users()

    def load_tracked_messages(self) -> list:
        if os.path.exists(TRACKED_MESSAGES_PATH):
            try:
                with open(TRACKED_MESSAGES_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_tracked_messages(self):
        try:
            with open(TRACKED_MESSAGES_PATH, "w", encoding="utf-8") as f:
                json.dump(self.tracked_messages, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def track_message(self, chat_id: int | str, message_id: int, tag: str = "bet"):
        """Lưu lại message_id và chat_id để tự động xóa sau 24h"""
        if not chat_id or not message_id:
            return
        try:
            cid = str(chat_id)
            mid = int(message_id)
            for item in self.tracked_messages:
                if str(item.get("chat_id")) == cid and item.get("message_id") == mid:
                    return
            self.tracked_messages.append({
                "chat_id": cid,
                "message_id": mid,
                "created_at": time.time(),
                "tag": tag
            })
            self.save_tracked_messages()
        except Exception as e:
            self.log(f"Lỗi theo dõi tin nhắn #{message_id}: {e}", "WARN")

    def delete_telegram_message(self, chat_id: int | str, message_id: int) -> tuple[bool, str]:
        """Gọi API Telegram deleteMessage để xóa tin nhắn"""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return False, "Chưa nhập Bot Token"
        url = f"https://api.telegram.org/bot{token}/deleteMessage"
        payload = {
            "chat_id": str(chat_id),
            "message_id": int(message_id)
        }
        try:
            resp = requests.post(url, json=payload, timeout=8)
            data = resp.json()
            if data.get("ok"):
                return True, ""
            desc = data.get("description", "Không rõ lỗi")
            # Nếu tin nhắn đã bị xóa trước đó thì coi như thành công
            if "message to delete not found" in desc.lower():
                return True, desc
            return False, desc
        except Exception as e:
            return False, str(e)

    def clean_old_logs(self, max_age_seconds: int = 86400):
        """Xóa các dòng log cũ hơn 24h trong bot_activity.log để bảo mật tuyệt đối"""
        if not os.path.exists(LOG_PATH):
            return
        try:
            now = datetime.now()
            kept_lines = []
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            
            current_entry_keep = True
            for line in lines:
                if len(line) >= 17 and line[0] == '[' and line[9] == ' ' and line[15] == ']':
                    date_str = line[10:15]  # DD/MM
                    time_str = line[1:9]    # HH:MM:SS
                    try:
                        d, m = map(int, date_str.split('/'))
                        h, mi, s = map(int, time_str.split(':'))
                        from datetime import timedelta
                        entry_dt = datetime(now.year, m, d, h, mi, s)
                        if entry_dt > now + timedelta(days=1):
                            entry_dt = entry_dt.replace(year=now.year - 1)
                        age_sec = (now - entry_dt).total_seconds()
                        current_entry_keep = (0 <= age_sec <= max_age_seconds)
                    except Exception:
                        current_entry_keep = True
                if current_entry_keep:
                    kept_lines.append(line)
            
            if len(kept_lines) < len(lines):
                with open(LOG_PATH, "w", encoding="utf-8") as f:
                    f.writelines(kept_lines)
        except Exception:
            pass

    def check_and_cleanup_traces(self, force: bool = False) -> tuple[int, int]:
        """Tự động kiểm tra và xóa dấu vết cược sau 24h"""
        cleanup_seconds = int(self.config.get("cleanup_after_seconds", 86400))
        now = time.time()
        remaining = []
        deleted_count = 0

        for item in self.tracked_messages:
            item_time = item.get("created_at", 0)
            if force or (now - item_time >= cleanup_seconds):
                cid = item.get("chat_id")
                mid = item.get("message_id")
                ok, err = self.delete_telegram_message(cid, mid)
                if ok:
                    deleted_count += 1
                else:
                    # Nếu lỗi vĩnh viễn không thể xóa (ví dụ quá 48h, hoặc giới hạn Telegram)
                    if "can't be deleted" in err.lower() or "not found" in err.lower():
                        deleted_count += 1
                    else:
                        remaining.append(item)
            else:
                remaining.append(item)

        h_label = f"{round(cleanup_seconds/3600, 1):g}h"
        if deleted_count > 0:
            self.tracked_messages = remaining
            self.save_tracked_messages()
            self.log(f"🗑️ [Tự động xóa {h_label}] Đã xóa {deleted_count} tin nhắn cược trên Telegram.", "INFO")

        # Xóa nhật ký log cũ hơn cleanup_seconds
        self.clean_old_logs(max_age_seconds=cleanup_seconds)

        # Xóa dữ liệu cược trên bảng nếu sau cleanup_seconds không có hoạt động
        if self.last_bet_timestamp and (now - self.last_bet_timestamp >= cleanup_seconds):
            has_bets = (len(self.balancer.de_sums) > 0 or len(self.balancer.lo_sums) > 0 or 
                        len(self.balancer.bacang_sums) > 0 or len(self.balancer.xien_bets) > 0)
            if has_bets:
                self.balancer.reset_board()
                self.client_msg_counters = {}
                self.last_bet_timestamp = None
                self.log(f"🗑️ [Tự động xóa {h_label}] Đã tự động reset bảng cược và số thứ tự tin về 0 sau {h_label} không có hoạt động.", "INFO")

        return deleted_count, len(self.tracked_messages)

    def resolve_recipient(self, recipient_input: str) -> tuple[str, str]:
        """
        Phân giải recipient từ input (có thể là Chat ID số, hoặc @username).
        Trả về (chat_id, error_message).
        """
        raw = str(recipient_input).strip()
        if not raw:
            return "", "Chưa nhập thông tin người nhận."

        # Nếu là số điện thoại (bắt đầu bằng +84 hoặc 0 và có từ 9 chữ số)
        cleaned_num = raw.replace(" ", "").replace("-", "")
        if (cleaned_num.startswith("+84") and len(cleaned_num) >= 11) or (cleaned_num.startswith("0") and len(cleaned_num) >= 10):
            return "", "Telegram Bot KHÔNG THỂ gửi tin bằng Số điện thoại! Bạn cần lấy số Chat ID (ví dụ: 123456789) hoặc bảo người đó mở Bot bấm /start."

        # Nếu là @username
        bot_uname = (self.stats.get("bot_info") or {}).get("username") or "bot"
        if raw.startswith("@") or not raw.lstrip("-").isdigit():
            uname = raw.lstrip("@").lower()
            # Tra trong known_users
            for uid, info in self.known_users.items():
                if (info.get("username") or "").lower() == uname:
                    return str(info.get("chat_id")), ""
            # Chưa từng nhắn cho bot
            return "", f"Người dùng @{uname} chưa từng mở chat hoặc bấm /start với @{bot_uname}. Hãy bảo @{uname} tìm @{bot_uname} trên Telegram và bấm /start trước, hoặc điền trực tiếp số Chat ID."

        # Nếu là số nguyên (hoặc số âm với nhóm Telegram)
        if raw.lstrip("-").isdigit():
            return raw, ""

        return "", f"Không nhận diện được người nhận '{raw}'. Vui lòng nhập Chat ID dạng số hoặc @username hợp lệ."

    def log(self, message: str, level: str = "INFO"):
        now_str = datetime.now().strftime("%H:%M:%S %d/%m")
        entry = {
            "timestamp": now_str,
            "level": level,
            "message": message
        }
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs.pop(0)

        # Ghi log ra file
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] [{level}] {message}\n")
        except Exception:
            pass

    def send_telegram_message(self, recipient: str, text: str, track_for_cleanup: bool = False, tag: str = "outgoing") -> tuple[bool, str]:
        """Gửi tin nhắn qua Telegram Bot API. Trả về (thành công, thông điệp lỗi nếu có)"""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return False, "Chưa nhập Bot Token."

        chat_id, err = self.resolve_recipient(recipient)
        if not chat_id:
            self.log(f"Lỗi gửi tin tới {recipient}: {err}", "WARN")
            return False, err

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "Lỗi Telegram không xác định")
                bot_uname = (self.stats.get("bot_info") or {}).get("username") or "bot"
                if "chat not found" in desc.lower():
                    friendly_err = f"Telegram báo 'chat not found': Người nhận ({recipient}) chưa từng bấm /start với @{bot_uname} hoặc sai Chat ID."
                elif "blocked" in desc.lower():
                    friendly_err = f"Người nhận ({recipient}) đã chặn (block) Bot."
                else:
                    friendly_err = f"Lỗi Telegram: {desc}"
                self.log(f"Lỗi gửi tin tới {recipient} (Chat ID: {chat_id}): {friendly_err}", "WARN")
                return False, friendly_err

            # Nếu bật theo dõi để tự động xóa sau 24h
            if track_for_cleanup:
                sent_msg = data.get("result", {})
                sent_mid = sent_msg.get("message_id")
                sent_cid = sent_msg.get("chat", {}).get("id") or chat_id
                if sent_mid and sent_cid:
                    self.track_message(sent_cid, sent_mid, tag=tag)

            return True, ""
        except Exception as e:
            err_msg = f"Lỗi kết nối máy chủ Telegram: {e}"
            self.log(f"Lỗi kết nối tới {recipient}: {err_msg}", "ERROR")
            return False, err_msg

    def check_bot_token(self) -> dict:
        """Kiểm tra tính hợp lệ của Bot Token qua getMe"""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return {"valid": False, "error": "Chưa nhập Bot Token"}

        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            resp = requests.get(url, timeout=6)
            data = resp.json()
            if data.get("ok"):
                self.stats["bot_info"] = data.get("result")
                return {"valid": True, "info": data.get("result")}
            else:
                err = data.get("description", "Token không hợp lệ")
                self.stats["error_message"] = err
                return {"valid": False, "error": err}
        except Exception as e:
            err = f"Không kết nối được server Telegram: {e}"
            self.stats["error_message"] = err
            return {"valid": False, "error": err}

    def is_sender_allowed(self, user_id: int, username: str) -> bool:
        """Kiểm tra người gửi có nằm trong danh sách khách được chỉ định không"""
        allowed = self.config.get("allowed_senders", [])
        if not allowed or "*" in allowed or "" in allowed:
            return True  # Cho phép tất cả nếu danh sách để trống hoặc chứa *

        # Chuẩn hóa
        uid_str = str(user_id)
        uname_str = ("@" + username.lstrip("@").lower()) if username else ""

        for item in allowed:
            item_str = str(item).strip().lower()
            if item_str == uid_str:
                return True
            if uname_str and (item_str == uname_str or item_str.lstrip("@") == uname_str.lstrip("@")):
                return True

        return False

    def handle_incoming_message(self, message: dict):
        """Xử lý một tin nhắn nhận được từ khách"""
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type", "private")
        is_group = chat_type in ["group", "supergroup"]
        group_title = f" [Nhóm: {chat.get('title')}]" if is_group else ""

        from_user = message.get("from", {})
        user_id = from_user.get("id")
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        text = (message.get("text") or message.get("caption") or "").strip()

        if not text:
            return

        # Lưu người dùng vào known_users
        self.record_user(from_user, chat_id)

        sender_label = f"{first_name} (@{username})" if username else f"{first_name} (ID: {user_id})"

        # Xử lý các lệnh điều khiển hệ thống
        cmd = text.lower().strip()
        
        # 1. /start hoặc /id
        if cmd in ["/start", "start", "/id", "id"] or cmd.startswith("/start@") or cmd.startswith("/id@"):
            reply = (
                f"👋 <b>Xin chào {first_name}!</b>\n\n"
                f"🆔 <b>Chat ID của bạn:</b> <code>{chat_id}</code>\n"
                f"👤 <b>Username:</b> @{username if username else '(Chưa có)'}\n\n"
                f"👉 <i>Hãy sao chép số Chat ID <code>{chat_id}</code> này dán vào ô <b>'Người nhận cược thừa'</b> hoặc <b>'Khách được chỉ định'</b> trên Web nhé!</i>\n\n"
                f"💡 <i>Gõ /help để xem danh sách các lệnh quản lý.</i>"
            )
            self.send_telegram_message(str(chat_id), reply)
            self.log(f"Người dùng {sender_label} đã kết nối và nhận Chat ID: {chat_id}", "INFO")
            return

        # 2. /help hoặc help
        if cmd in ["/help", "help", "/menu", "menu"] or cmd.startswith("/help@"):
            help_text = (
                "🤖 <b>CÁC LỆNH ĐIỀU KHIỂN HỆ THỐNG:</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🎯 <b>/kqxs</b>: Xem Kết quả Xổ số Miền Bắc hôm nay\n"
                "📊 <b>/baocao</b>: Xem Báo cáo Thầu / Giữ lại / Chuyển\n"
                "📋 <b>/bang</b>: Xem tổng cược tích lũy hôm nay\n"
                "🚀 <b>/chuyen</b>: Bắn ngay các cược vượt định mức\n"
                "⚙️ <b>/gia</b>: Xem cấu hình bảng giá & hoa hồng\n"
                "⏰ <b>/timer &lt;giờ&gt;</b>: Đổi số giờ tự động xóa vết cược (VD: /timer 12)\n"
                "🧹 <b>/clean</b>: Xóa dấu vết tin cược cũ ngay lập tức\n"
                "🗑️ <b>/reset</b>: Xóa cược bắt đầu ngày mới\n"
                "🆔 <b>/id</b>: Xem Chat ID của bạn\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<i>Chỉ cần nhắn tin cược (VD: de 88x100, lo 12x20d) để bot tự động nhận & cân bảng!</i>"
            )
            self.send_telegram_message(str(chat_id), help_text)
            return

        # 3. /kqxs hoặc kết quả
        if cmd in ["/kqxs", "kqxs", "kết quả", "ket qua", "kq"] or cmd.startswith("/kqxs@"):
            self.log(f"{sender_label} yêu cầu lấy KQXS", "INFO")
            kq = fetch_xsmb()
            self.cached_kqxs = kq
            msg = format_xsmb_message(kq)
            self.send_telegram_message(str(chat_id), msg)
            return

        # 4. /baocao hoặc báo cáo
        if cmd in ["/baocao", "baocao", "báo cáo", "bao cao", "/bc", "bc"] or cmd.startswith("/baocao@"):
            self.log(f"{sender_label} yêu cầu xuất báo cáo", "INFO")
            kq = self.cached_kqxs or fetch_xsmb()
            self.cached_kqxs = kq
            acc = calculate_board_accounting(self.balancer, kq, self.config.get("price_config"))

            thau_rep = format_accounting_report(acc, "thau")
            giulai_rep = format_accounting_report(acc, "giulai")
            chuyen_rep = format_accounting_report(acc, "chuyen")

            reply_msg = (
                f"📊 <b>BÁO CÁO THẦU HÔM NAY</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 <b>BẢNG THẦU:</b>\n{thau_rep}\n\n"
                f"🛡️ <b>GIỮ LẠI (Ăn thua):</b>\n{giulai_rep}\n\n"
                f"🛸 <b>CHUYỂN THẦU TRÊN:</b>\n{chuyen_rep}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👑 Đề về: <b>{kq.get('special_last2', '--')}</b> | 3C: <b>{kq.get('special_last3', '---')}</b>"
            )
            self.send_telegram_message(str(chat_id), reply_msg)
            return

        # 5. /bang hoặc /canbang
        if cmd in ["/bang", "bảng", "bang", "/canbang", "cân bảng", "can bang"] or cmd.startswith("/bang@"):
            b = self.balancer
            de_c = len(b.de_sums)
            de_s = sum(b.de_sums.values())
            lo_c = len(b.lo_sums)
            lo_s = sum(b.lo_sums.values())
            bc_c = len(b.bacang_sums)
            bc_s = sum(b.bacang_sums.values())
            x_c = len(b.xien_bets)
            x_s = sum(x.get("amount", 0) for x in b.xien_bets)

            lines = [
                "📋 <b>BẢNG CƯỢC TÍCH LŨY HÔM NAY:</b>",
                "━━━━━━━━━━━━━━━━━━",
                f"🔴 <b>Đề:</b> {de_c} con (Tổng: {de_s:,.0f}k)",
                f"🔵 <b>Lô:</b> {lo_c} con (Tổng: {lo_s:,.0f}đ)",
                f"🟣 <b>3 Càng:</b> {bc_c} con (Tổng: {bc_s:,.0f}k)",
                f"🟢 <b>Xiên:</b> {x_c} cặp (Tổng: {x_s:,.0f}k)",
                f"🛸 <b>Đã bắn cược:</b> {b.step_count} lần",
                "━━━━━━━━━━━━━━━━━━"
            ]
            if de_c > 0:
                top_de = sorted(b.de_sums.items(), key=lambda x: x[1], reverse=True)[:5]
                top_de_str = ", ".join(f"{k}x{v:,.0f}" for k, v in top_de)
                lines.append(f"• Top Đề: {top_de_str}")
            if lo_c > 0:
                top_lo = sorted(b.lo_sums.items(), key=lambda x: x[1], reverse=True)[:5]
                top_lo_str = ", ".join(f"{k}x{v:,.0f}đ" for k, v in top_lo)
                lines.append(f"• Top Lô: {top_lo_str}")

            self.send_telegram_message(str(chat_id), "\n".join(lines))
            return

        # 6. /chuyen
        if cmd in ["/chuyen", "chuyen", "bắn cược", "ban cuoc"] or cmd.startswith("/chuyen@"):
            excess = self.balancer.calculate_excess()
            excess_count = sum(len(v) for v in excess.values())
            if excess_count == 0:
                self.send_telegram_message(str(chat_id), "🛡️ Hiện tại bảng cược không có số nào vượt định mức giữ lại.")
                return
            target_recipient = self.config.get("target_recipient", "").strip()
            if not target_recipient:
                self.send_telegram_message(str(chat_id), "⚠️ Chưa cài đặt người nhận cược thừa (target_recipient) trên Web!")
                return
            transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu Chuyển")
            ok, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
            if ok:
                self.balancer.commit_transfers(excess)
                self.stats["transfers_sent"] += 1
                self.send_telegram_message(str(chat_id), f"🚀 Đã bắn {excess_count} con cược thừa sang {target_recipient} thành công!")
            else:
                self.send_telegram_message(str(chat_id), f"❌ Bắn cược thừa thất bại: {err}")
            return

        # 7. /reset
        if cmd in ["/reset", "reset", "xóa cược", "xoa cuoc"] or cmd.startswith("/reset@"):
            self.balancer.reset_board()
            self.client_msg_counters = {}
            self.log("Đã reset bảng cược và số thứ tự tin về 0 theo lệnh Telegram", "INFO")
            self.send_telegram_message(str(chat_id), "🗑️ Đã làm mới (reset) toàn bộ bảng cược và số thứ tự tin về 0 để bắt đầu ngày mới!")
            return

        # 8. /clean hoặc /xoadauvet
        if cmd in ["/clean", "clean", "/xoadauvet", "xoa dau vet"] or cmd.startswith("/clean@"):
            del_c, rem_c = self.check_and_cleanup_traces()
            cur_h = round(self.config.get("cleanup_after_seconds", 86400) / 3600, 1)
            self.send_telegram_message(str(chat_id), f"🧹 Đã rà soát dấu vết: Xóa {del_c} tin nhắn cược cũ, hiện còn {rem_c} tin đang hẹn giờ tự động xóa (chu kỳ {cur_h:g} giờ).")
            return

        # 9. /timer hoặc /thoigianxoa
        if cmd.startswith("/timer") or cmd.startswith("/thoigianxoa") or cmd.startswith("/gio "):
            parts = cmd.split()
            if len(parts) >= 2:
                try:
                    hours = float(parts[1].replace(",", "."))
                    if hours <= 0:
                        self.send_telegram_message(str(chat_id), "⚠️ Số giờ phải lớn hơn 0.")
                        return
                    secs = int(hours * 3600)
                    self.config["cleanup_after_seconds"] = secs
                    self.config["cleanup_after_hours"] = hours
                    self.save_config()
                    self.log(f"Đã cập nhật thời gian tự động xóa dấu vết sang {hours:g} giờ", "INFO")
                    self.send_telegram_message(str(chat_id), f"⏰ <b>Thành công:</b> Đã cài đặt thời gian tự động xóa vết cược là <b>{hours:g} giờ</b>!")
                    return
                except ValueError:
                    self.send_telegram_message(str(chat_id), "⚠️ Cú pháp không hợp lệ. Ví dụ: <code>/timer 12</code> hoặc <code>/timer 6</code>")
                    return
            else:
                cur_h = round(self.config.get("cleanup_after_seconds", 86400) / 3600, 1)
                self.send_telegram_message(str(chat_id), f"⏰ Thời gian tự động xóa dấu vết hiện tại là: <b>{cur_h:g} giờ</b>.\nĐể đổi, hãy gõ ví dụ: <code>/timer 12</code> hoặc <code>/timer 6</code>")
                return

        # 10. /gia hoặc /banggia
        if cmd in ["/gia", "gia", "/banggia", "bang gia", "/rates"] or cmd.startswith("/gia@"):
            summary_msg = format_price_config_summary(self.config.get("price_config"))
            self.send_telegram_message(str(chat_id), summary_msg)
            return

        # 1. Kiểm tra quyền của khách
        if not self.is_sender_allowed(user_id, username):
            self.log(f"⚠️ Từ chối tin từ '{sender_label}' (ID: {user_id}){group_title}: Người gửi KHÔNG có trong danh sách Khách chỉ định! (Để nhận từ tất cả, hãy điền dấu * vào ô Khách chỉ định)", "WARN")
            return

        self.stats["messages_received"] += 1
        self.stats["last_active"] = datetime.now().strftime("%H:%M:%S")

        self.log(f"📩 Nhận tin cược từ {sender_label}{group_title}:\n{text}")

        # 2. Phân tích cược
        user_msg_id = message.get("message_id")
        parsed = parse_bet_message(text)
        summary = parsed.get("summary", {})
        total_bets_count = summary.get("de_count", 0) + summary.get("lo_count", 0) + summary.get("bacang_count", 0) + summary.get("xien_count", 0)

        if total_bets_count == 0:
            if parsed.get('invalid_items') and self.config.get("auto_reply_client", True):
                unique_inv = list(dict.fromkeys(parsed['invalid_items']))
                joined_inv = " ".join(unique_inv) if all(x.isdigit() for x in unique_inv) else ", ".join(unique_inv)
                self.send_telegram_message(str(chat_id), f"Trả lại {joined_inv}", track_for_cleanup=True, tag="invalid_receipt")
            self.log(f"Tin nhắn từ {sender_label}{group_title} không chứa cú pháp cược hợp lệ: '{text}'", "INFO")
            return

        # Lưu vết tin nhắn cược của khách để tự động xóa sau 24h
        if user_msg_id and chat_id:
            self.track_message(chat_id, user_msg_id, tag="incoming_bet")
            self.last_bet_timestamp = time.time()

        # Tăng số thứ tự tin của khách (Ok 1, Ok 2...)
        chat_id_str = str(chat_id)
        msg_idx = self.client_msg_counters.get(chat_id_str, 0) + 1
        self.client_msg_counters[chat_id_str] = msg_idx

        # 3. Phản hồi xác nhận chi tiết cho khách (nếu bật)
        if self.config.get("auto_reply_client", True):
            receipt_text = format_ok_receipt(parsed, msg_idx)
            if len(receipt_text) > 3800:
                chunks = [receipt_text[i:i+3800] for i in range(0, len(receipt_text), 3800)]
                for chunk in chunks:
                    self.send_telegram_message(chat_id_str, chunk, track_for_cleanup=True, tag="receipt")
            else:
                self.send_telegram_message(chat_id_str, receipt_text, track_for_cleanup=True, tag="receipt")

        # 4. Cân bảng và tính phần cược thừa
        excess = self.balancer.add_bets(parsed)
        excess_count = sum(len(v) for v in excess.values())

        self.log(f"⚖️ Cân bảng sau tin {sender_label}: Vượt mức giữ lại {excess_count} con cược.")

        # 5. Nếu có cược thừa và bật chế độ tự động bắn (instant)
        if self.config.get("auto_forward_excess", True) and self.config.get("mode", "instant") == "instant":
            if excess_count > 0:
                target_recipient = self.config.get("target_recipient", "").strip()
                if target_recipient:
                    transfer_msg = self.balancer.format_transfer_message(excess, header_prefix="Thầu")
                    success, err = self.send_telegram_message(target_recipient, transfer_msg, track_for_cleanup=True, tag="transfer")
                    if success:
                        self.balancer.commit_transfers(excess)
                        self.stats["transfers_sent"] += 1
                        self.log(f"🚀 Đã tự động bắn cược thừa tới {target_recipient}:\n{transfer_msg}", "SUCCESS")
                    else:
                        self.log(f"❌ Thất bại khi gửi cược thừa tới {target_recipient}: {err}", "ERROR")
                else:
                    self.log("⚠️ Có cược thừa nhưng chưa thiết lập người nhận (target_recipient)!", "WARN")
            else:
                self.log(f"🛡️ Toàn bộ cược nằm trong định mức giữ lại, không có cược thừa cần chuyển.", "INFO")

    def check_daily_schedule(self):
        """Kiểm tra thời gian và tự động cào KQXS lúc 18h30 - 18h45"""
        if not self.config.get("auto_fetch_kqxs_daily", True):
            return

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Khung giờ quay thưởng XSMB từ 18:30 đến 18:45
        if now.hour == 18 and 30 <= now.minute <= 48:
            if self.last_daily_report_date != today_str:
                # Kiểm tra sau mỗi 60 giây
                last_check = getattr(self, "_last_kqxs_check_ts", 0)
                if time.time() - last_check < 60:
                    return
                self._last_kqxs_check_ts = time.time()

                self.log(f"⏰ Đang lấy KQXS hôm nay ({now.strftime('%H:%M:%S')})...", "INFO")
                kq = fetch_xsmb()
                if kq.get("is_complete"):
                    self.cached_kqxs = kq
                    self.last_daily_report_date = today_str
                    self.log(f"🎯 Đã có đầy đủ KQXS 27 giải ngày {kq.get('date')}! Tự động tính báo cáo...", "SUCCESS")
                    acc = calculate_board_accounting(self.balancer, kq, self.config.get("price_config"))
                    thau_report = format_accounting_report(acc, "thau")
                    giulai_report = format_accounting_report(acc, "giulai")
                    
                    full_report = (
                        f"🏆 <b>BÁO CÁO THẦU TỔNG KẾT - {kq.get('date')}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📋 <b>BẢNG THẦU:</b>\n{thau_report}\n\n"
                        f"🛡️ <b>GIỮ LẠI (Ăn thua):</b>\n{giulai_report}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"👑 Đề: <b>{kq.get('special_last2')}</b> | 3C: <b>{kq.get('special_last3')}</b>"
                    )

                    send_to = self.config.get("owner_chat_id") or self.config.get("target_recipient")
                    if send_to:
                        self.send_telegram_message(send_to, full_report, track_for_cleanup=True, tag="daily_report")
                        self.log(f"Đã tự động gửi Báo cáo tổng kết ngày {today_str} tới {send_to}", "SUCCESS")

    def poll_updates(self):
        """Vòng lặp Long Polling nhận tin nhắn liên tục"""
        self.log("Bot Telegram bắt đầu lắng nghe tin cược...")
        token = self.config.get("bot_token", "").strip()

        while self.is_running:
            try:
                # Tự động rà soát và xóa dấu vết cược sau 24h (chạy mỗi 60 giây)
                now_ts = time.time()
                if now_ts - getattr(self, "last_cleanup_ts", 0) >= 60:
                    self.last_cleanup_ts = now_ts
                    try:
                        self.check_and_cleanup_traces()
                    except Exception as ex:
                        self.log(f"Lỗi tự động xóa dấu vết cược: {ex}", "WARN")

                # Tự động kiểm tra lịch KQXS 18h30
                try:
                    self.check_daily_schedule()
                except Exception as ex:
                    pass

                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 15
                }
                resp = requests.get(url, params=params, timeout=20)
                data = resp.json()

                if data.get("ok"):
                    for update in data.get("result", []):
                        self.last_update_id = update.get("update_id", self.last_update_id)
                        message = update.get("message") or update.get("channel_post") or update.get("edited_message")
                        if message:
                            self.handle_incoming_message(message)
                else:
                    err = data.get("description", "")
                    self.log(f"Lỗi getUpdates: {err}", "WARN")
                    time.sleep(3)

            except requests.exceptions.Timeout:
                # Timeout bình thường của Long Polling
                continue
            except Exception as e:
                self.log(f"Lỗi kết nối polling: {e}", "ERROR")
                time.sleep(3)

        self.log("Bot Telegram đã dừng lắng nghe.")

    def start(self) -> dict:
        """Bật bot chạy ngầm"""
        if self.is_running:
            return {"status": "already_running"}

        check = self.check_bot_token()
        if not check.get("valid"):
            return {"status": "error", "message": check.get("error")}

        self.is_running = True
        self.polling_thread = threading.Thread(target=self.poll_updates, daemon=True)
        self.polling_thread.start()
        return {"status": "started", "info": check.get("info")}

    def stop(self):
        """Dừng bot"""
        self.is_running = False
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=2)
        return {"status": "stopped"}


# Singleton instance
bot_service = TelegramBotService()
if bot_service.config.get("bot_token"):
    try:
        bot_service.start()
    except Exception:
        pass
