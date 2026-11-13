"""
Giới hạn tiền đánh theo từng con số.

Cú pháp cực kỳ đơn giản (mỗi dòng một luật):
    <loại> <các số> x <số tiền>
    hoặc: <loại> <các số> = <số tiền>

Ví dụ:
    de 36.63x100
    lo 12x50
    3c 123x20
    xien 12-34x50
    de kepx200

Ý nghĩa:
- Tích ô "Giới hạn tổng cả bảng": số tiền đó là mức trần của cả bảng.
- Tích ô "Giới hạn từng khách": số tiền đó là mức trần của mỗi khách.
- Tích cả 2 ô: áp dụng cả hai (vừa trần cả bảng, vừa trần từng khách).
- Hỗ trợ cú pháp nâng cao nếu cần mức khác nhau giữa cả bảng và từng khách:
    de 36.63 = 500 | 100
"""

import re

from bet_parser import resolve_shorthands, update_parsed_summary

_TYPE_ALIASES = {
    "de": "de", "đe": "de", "đề": "de", "đê": "de", "d": "de",
    "lo": "lo", "lô": "lo", "l": "lo",
    "3c": "bacang", "3cang": "bacang", "3càng": "bacang", "bacang": "bacang",
    "bc": "bacang", "cang": "bacang", "càng": "bacang", "c": "bacang",
    "xien": "xien", "xiên": "xien", "x": "xien",
}

_CATS_SIMPLE = ("de", "lo", "bacang")
_CATS_XIEN = ("xien2", "xien3", "xien4")


def _to_amount(s: str):
    if s is None:
        return None
    s = str(s).strip().lower().replace(",", ".")
    s = re.sub(r"[kđdn₫]+$", "", s).strip()
    if not s or s in ("-", "*", "∞", "inf", "khong", "không"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v >= 0 else None


def _xien_key(numbers) -> str:
    return "-".join(sorted(str(x).zfill(2) for x in numbers))


def parse_number_limit_rules(text: str):
    """Trả về (rules, errors).

    rules: {(cat, key): (global_max | None, client_max | None)}
      cat in 'de' | 'lo' | 'bacang' | 'xien'
    """
    rules = {}
    errors = []
    for ln_no, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        cat = None
        num_text = ""
        g_max = None
        c_max = None

        # 1. Cú pháp nâng cao có dấu '|': ví dụ "de 36.63 = 500 | 100" hoặc "de 36x500 | 100"
        if "|" in line:
            parts = line.split("|", 1)
            left = parts[0].strip()
            c_max = _to_amount(parts[1])

            # Tách left thành [loại + số] và [tiền]
            m_left = re.match(r"^\s*([a-zA-ZđĐêÊ]+)\s+(.+?)(?:[xX=*]|\s*=\s*)\s*(\d+(?:\.\d+)?(?:[kđdn₫])?)\s*$", left)
            if m_left:
                cat = _TYPE_ALIASES.get(m_left.group(1).lower())
                num_text = m_left.group(2).strip()
                g_max = _to_amount(m_left.group(3))
            else:
                errors.append(f"Dòng {ln_no}: cú pháp không hợp lệ ({line})")
                continue
        else:
            # 2. Cú pháp tự nhiên đơn giản: "de 36x100", "de 36.63x100", "lo 12=50", "3c 123 x 20", "de kep x 200"
            m = re.match(r"^\s*([a-zA-ZđĐêÊ0-9]+)\s+(.+?)(?:[xX=*]|\s*=\s*)\s*(\d+(?:\.\d+)?(?:[kđdn₫])?)\s*$", line)
            if not m:
                # Thử tìm xem có loại đứng đầu và dấu = / x ở cuối không
                m = re.match(r"^\s*(\S+)\s+(.+?)\s+([xX=])\s*(\d+(?:\.\d+)?(?:[kđdn₫])?)\s*$", line)

            if m:
                raw_type = m.group(1).lower()
                cat = _TYPE_ALIASES.get(raw_type)
                num_text = m.group(2).strip()
                amt = _to_amount(m.group(3) if len(m.groups()) == 3 else m.group(4))
                # Khi chỉ nhập 1 mức tiền (ví dụ x100), gán cả g_max và c_max bằng mức đó
                # Lúc kiểm tra, tích ô nào thì ô đó dùng mức này!
                g_max = amt
                c_max = amt
            else:
                errors.append(f"Dòng {ln_no}: cú pháp phải có dạng 'de 36x100' hoặc 'de 36=100' ({line})")
                continue

        if not cat:
            errors.append(f"Dòng {ln_no}: loại cược không hợp lệ ({line})")
            continue

        if g_max is None and c_max is None:
            errors.append(f"Dòng {ln_no}: chưa có mức tiền giới hạn ({line})")
            continue

        keys = []
        if cat == "xien":
            for tok in re.split(r"[,\s;]+", num_text):
                nums = [n for n in re.split(r"[-+.]", tok) if n]
                if 2 <= len(nums) <= 4 and all(n.isdigit() and len(n) == 2 for n in nums):
                    keys.append(_xien_key(nums))
                elif tok:
                    errors.append(f"Dòng {ln_no}: tổ hợp xiên '{tok}' không hợp lệ (ví dụ: 12-34)")
        else:
            want = 3 if cat == "bacang" else 2
            try:
                resolved = resolve_shorthands(num_text)
            except Exception:
                resolved = num_text
            for tok in re.split(r"[.,;\s]+", resolved):
                if not tok:
                    continue
                if tok.isdigit() and len(tok) == want:
                    keys.append(tok)
                elif want == 2 and tok.isdigit() and len(tok) == 3 and tok[0] == tok[2]:
                    # Cặp lộn đối xứng, ví dụ 010 -> 01, 10 hoặc 686 -> 68, 86 (như trong chạm, kép lệch...)
                    keys.append(tok[0:2])
                    keys.append(tok[1:3])
                else:
                    errors.append(f"Dòng {ln_no}: số '{tok}' không hợp lệ cho loại {cat}")

        for k in keys:
            rules[(cat, k)] = (g_max, c_max)

    return rules, errors


def _fmt_amt(v: float) -> str:
    v = round(v, 2)
    return str(int(v)) if float(v).is_integer() else str(v)


def split_by_number_limits(parsed: dict, rules: dict, glob: dict, cli: dict,
                           use_global: bool = True, use_client: bool = True):
    """Tách tin cược thành (accepted, rejected) theo hạn mức từng con số.

    glob / cli: tổng tiền ĐÃ có sẵn: {'de': {num: amt}, 'lo': {...}, 'bacang': {...}, 'xien': {key: amt}}
    Phần vượt hạn mức nằm trong `rejected` (trả lại khách).
    """
    accepted = {c: [] for c in _CATS_SIMPLE + _CATS_XIEN}
    rejected = {c: [] for c in _CATS_SIMPLE + _CATS_XIEN}
    accepted["invalid_items"] = list(parsed.get("invalid_items", []))
    run_g, run_c = {}, {}

    def room(cat, key, amount):
        rule = rules.get((cat, key))
        if not rule:
            return amount
        g_max, c_max = rule
        remain = float("inf")
        if use_global and g_max is not None:
            used = glob.get(cat, {}).get(key, 0.0) + run_g.get((cat, key), 0.0)
            remain = min(remain, g_max - used)
        if use_client and c_max is not None:
            used = cli.get(cat, {}).get(key, 0.0) + run_c.get((cat, key), 0.0)
            remain = min(remain, c_max - used)
        remain = max(0.0, round(remain, 6))
        return min(amount, remain)

    def handle(src_cat, item, cat, key):
        amount = float(item.get("amount", 0))
        ok_amt = room(cat, key, amount)
        ok_amt = round(ok_amt, 2)
        rest = round(amount - ok_amt, 2)
        if ok_amt > 0:
            acc = dict(item)
            acc["amount"] = ok_amt
            accepted[src_cat].append(acc)
            run_g[(cat, key)] = run_g.get((cat, key), 0.0) + ok_amt
            run_c[(cat, key)] = run_c.get((cat, key), 0.0) + ok_amt
        if rest > 0:
            rej = dict(item)
            rej["amount"] = rest
            rejected[src_cat].append(rej)

    for cat in _CATS_SIMPLE:
        width = 3 if cat == "bacang" else 2
        for b in parsed.get(cat, []):
            handle(cat, b, cat, str(b.get("number", "")).zfill(width))
    for cat in _CATS_XIEN:
        for b in parsed.get(cat, []):
            handle(cat, b, "xien", _xien_key(b.get("numbers", [])))

    update_parsed_summary(accepted)
    update_parsed_summary(rejected)
    return accepted, rejected


def count_bets(parsed: dict) -> int:
    s = parsed.get("summary", {})
    return (s.get("de_count", 0) + s.get("lo_count", 0)
            + s.get("bacang_count", 0) + s.get("xien_count", 0))
