import re
from itertools import combinations

# Bảng từ viết tắt (shorthands) đồng bộ với web index.html
SHORTHANDS = {
    # Đầu (dau0 - dau9)
    **{f"dau{i}": ".".join(f"{i}{j}" for j in range(10)) for i in range(10)},
    **{f"d{i}": ".".join(f"{i}{j}" for j in range(10)) for i in range(10)},
    
    # Đít / Đuôi (dit0 - dit9)
    **{f"dit{i}": ".".join(f"{j}{i}" for j in range(10)) for i in range(10)},
    **{f"duoi{i}": ".".join(f"{j}{i}" for j in range(10)) for i in range(10)},
    **{f"d'{i}": ".".join(f"{j}{i}" for j in range(10)) for i in range(10)},
    
    # Tổng (tong0 - tong9)
    **{f"tong{i}": ".".join(f"{a}{b}" for a in range(10) for b in range(10) if (a + b) % 10 == i) for i in range(10)},
    **{f"t{i}": ".".join(f"{a}{b}" for a in range(10) for b in range(10) if (a + b) % 10 == i) for i in range(10)},
    
    # Kép
    "kep": "00.11.22.33.44.55.66.77.88.99",
    "kepbang": "00.11.22.33.44.55.66.77.88.99",
    "kepl": "05.50.16.61.27.72.38.83.49.94",
    "keple": "05.50.16.61.27.72.38.83.49.94",
    "satkep": "01.10.12.21.23.32.34.43.45.54.56.65.67.76.78.87.89.98.09.90",
    
    # Chẵn / Lẻ
    "chanchan": ".".join(f"{a}{b}" for a in range(0, 10, 2) for b in range(0, 10, 2)),
    "lele": ".".join(f"{a}{b}" for a in range(1, 10, 2) for b in range(1, 10, 2)),
    "chanle": ".".join(f"{a}{b}" for a in range(0, 10, 2) for b in range(1, 10, 2)),
    "lechan": ".".join(f"{a}{b}" for a in range(1, 10, 2) for b in range(0, 10, 2)),
    
    # Bé / To (Bé: 0-4, To: 5-9)
    "beto": ".".join(f"{a}{b}" for a in range(0, 5) for b in range(5, 10)),
    "tobe": ".".join(f"{a}{b}" for a in range(5, 10) for b in range(0, 5)),
    "bebe": ".".join(f"{a}{b}" for a in range(0, 5) for b in range(0, 5)),
    "toto": ".".join(f"{a}{b}" for a in range(5, 10) for b in range(5, 10)),
}

# Bộ số (hệ số)
BOS = {
    "00": ["00", "55", "05", "50"],
    "11": ["11", "66", "16", "61"],
    "22": ["22", "77", "27", "72"],
    "33": ["33", "88", "38", "83"],
    "44": ["44", "99", "49", "94"],
    "01": ["01", "10", "06", "60", "51", "15", "56", "65"],
    "02": ["02", "20", "07", "70", "52", "25", "57", "75"],
    "03": ["03", "30", "08", "80", "53", "35", "58", "85"],
    "04": ["04", "40", "09", "90", "54", "45", "59", "95"],
    "12": ["12", "21", "17", "71", "62", "26", "67", "76"],
    "13": ["13", "31", "18", "81", "63", "36", "68", "86"],
    "14": ["14", "41", "19", "91", "64", "46", "69", "96"],
    "23": ["23", "32", "28", "82", "73", "37", "78", "87"],
    "24": ["24", "42", "29", "92", "74", "47", "79", "97"],
    "34": ["34", "43", "39", "93", "84", "48", "89", "98"],
}
for k, nums in BOS.items():
    SHORTHANDS[f"bo{k}"] = ".".join(nums)
    SHORTHANDS[f"he{k}"] = ".".join(nums)
    SHORTHANDS[f"b{k}"] = ".".join(nums)


def normalize_bet_text(text: str) -> str:
    """Chuẩn hóa ký tự, dấu phẩy, khoảng trắng, tiền cược"""
    s = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'").replace("＇", "'").replace("`", "'")
    s = s.replace(",", " ").replace(";", " ")
    # Chuẩn hóa tiền dạng 'mc 50n', '50k', '50d', '50đ' -> 'x50'
    s = re.sub(r'\bmc\s*(\d+(?:\.\d+)?)(?:\s*(?:d|đ|₫|k|n))?\b', r'x\1', s, flags=re.I)
    s = re.sub(r'\s+(\d+(?:\.\d+)?)\s*(?:d|đ|₫|k|n)(?![a-z0-9])', r' x\1', s, flags=re.I)
    s = re.sub(r'(\d+)\s*k(?![a-z0-9])', r'\1', s, flags=re.I)
    return s


def expand_token(token: str) -> list[str]:
    """Mở rộng một token số hoặc từ viết tắt thành danh sách con số cụ thể"""
    t = token.strip().lower()
    if not t:
        return []

    # Kiểm tra trong bảng shorthands
    if t in SHORTHANDS:
        return SHORTHANDS[t].split(".")

    # Kiểm tra dạng số 2 chữ số hoặc 3 chữ số
    if t.isdigit():
        return [t]

    # Kiểm tra dạng 'dau1.dit2'
    if "." in t:
        parts = t.split(".")
        expanded = []
        for p in parts:
            expanded.extend(expand_token(p))
        return list(dict.fromkeys(expanded))

    return []


def parse_bet_message(raw_text: str) -> dict:
    """
    Phân tích một tin nhắn cược hoàn chỉnh của khách.
    Trả về dict phân loại:
    {
        'de': [{'number': '12', 'amount': 50.0}, ...],
        'lo': [{'number': '01', 'amount': 10.0}, ...],
        'bacang': [{'number': '123', 'amount': 20.0}, ...],
        'xien2': [{'numbers': ['12', '34'], 'amount': 100.0}, ...],
        'xien3': [{'numbers': ['12', '34', '56'], 'amount': 50.0}, ...],
        'xien4': [{'numbers': ['12', '34', '56', '78'], 'amount': 20.0}, ...],
        'summary': {'de_count': int, 'de_val': float, 'lo_count': int, 'lo_val': float, ...}
    }
    """
    cleaned = normalize_bet_text(raw_text)

    # Tách dòng nếu có từ khóa mới trên cùng 1 dòng
    keyword_split = re.compile(
        r'[\s.,;:\-]+(xienquay|xq|xienq|quay|q|xquay|lô\s*xiên|lo\s*xien|đề|de|đê|đe|dê|dè|đè|lô|lo|l|xiên|xien|x2|x3|x4|3cang|3c|cang|bc)(?=[\s.,;\-\d:]|$)',
        re.I
    )
    lines_raw = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        sublines = keyword_split.sub(r'\n\1', line).splitlines()
        for sub in sublines:
            if sub.strip():
                lines_raw.append(sub.strip())

    result = {
        'de': [],
        'lo': [],
        'bacang': [],
        'xien2': [],
        'xien3': [],
        'xien4': [],
    }

    type_regex = {
        'de': re.compile(r'^(de|d|đ|đề|đê|đe|dê|dè|đè)$', re.I),
        'lo': re.compile(r'^(lo|l|lô)$', re.I),
        'bacang': re.compile(r'^(3cang|3c|3càng|cang|càng|bc|c)$', re.I),
        'xienquay': re.compile(r'^(xienquay|xq|xienq|quay|q|xquay)$', re.I),
        'xien': re.compile(r'^(lo\s*xien|lô\s*xiên|xien|xiên|x|x2|x3|x4)$', re.I),
    }

    active_category = 'de'  # Mặc định đề nếu không ghi rõ

    for line in lines_raw:
        # Nhận diện thể loại ở đầu dòng
        m_cat = re.match(
            r'^(xienquay|xq|xienq|quay|q|xquay|lo\s*xien|lô\s*xiên|l\s*xien|de|đề|lo|lô|xien|xiên|x2|x3|x4|3cang|3càng|3c|bc|cang|càng|d|đ|l|c)\b[.:;\-\s]*(.*)$',
            line,
            re.I
        )
        rem = line
        if m_cat:
            cat_str = m_cat.group(1).lower()
            rem = m_cat.group(2).strip()
            if type_regex['de'].match(cat_str):
                active_category = 'de'
            elif type_regex['lo'].match(cat_str):
                active_category = 'lo'
            elif type_regex['bacang'].match(cat_str):
                active_category = 'bacang'
            elif type_regex['xienquay'].match(cat_str):
                active_category = 'xienquay'
            elif type_regex['xien'].match(cat_str):
                active_category = 'xien'
            if not rem:
                continue

        # Tách các cụm cược: [danh sách số] [x / + / *] [tiền]
        groups = re.findall(r'([a-zA-Z0-9\.\-\s\']+?)(?:[x\+\*])\s*(\d+(?:\.\d+)?)', rem)
        if not groups:
            # Thử tìm dạng: 12 34 50 (nếu không có chữ x)
            groups = re.findall(r'([a-zA-Z0-9\.\-\s\']+?)\s+(\d+(?:\.\d+)?)$', rem)

        for raw_nums, amount_str in groups:
            try:
                amt = float(amount_str)
            except ValueError:
                continue
            if amt <= 0:
                continue

            # Phân tách các số trong cụm
            raw_tokens = re.split(r'[\s.,;\-]+', raw_nums.strip())
            raw_tokens = [t for t in raw_tokens if t]

            if active_category == 'xienquay':
                expanded_numbers = []
                for tok in raw_tokens:
                    expanded_numbers.extend(expand_token(tok))
                expanded_numbers = [n.zfill(2) for n in expanded_numbers if len(n) <= 2]
                expanded_numbers = list(dict.fromkeys(expanded_numbers))

                if len(expanded_numbers) >= 2:
                    for combo in combinations(expanded_numbers, 2):
                        result['xien2'].append({'numbers': sorted(list(combo)), 'amount': amt})
                if len(expanded_numbers) >= 3:
                    for combo in combinations(expanded_numbers, 3):
                        result['xien3'].append({'numbers': sorted(list(combo)), 'amount': amt})
                if len(expanded_numbers) >= 4:
                    for combo in combinations(expanded_numbers, 4):
                        result['xien4'].append({'numbers': sorted(list(combo)), 'amount': amt})

            elif active_category == 'xien':
                expanded_numbers = []
                for tok in raw_tokens:
                    expanded_numbers.extend(expand_token(tok))
                expanded_numbers = [n.zfill(2) for n in expanded_numbers if len(n) <= 2]
                expanded_numbers = list(dict.fromkeys(expanded_numbers))

                if len(expanded_numbers) == 2:
                    result['xien2'].append({'numbers': sorted(expanded_numbers), 'amount': amt})
                elif len(expanded_numbers) == 3:
                    result['xien3'].append({'numbers': sorted(expanded_numbers), 'amount': amt})
                elif len(expanded_numbers) == 4:
                    result['xien4'].append({'numbers': sorted(expanded_numbers), 'amount': amt})

            elif active_category == 'bacang':
                for tok in raw_tokens:
                    expanded = expand_token(tok)
                    for n in expanded:
                        if len(n) == 3 and n.isdigit():
                            result['bacang'].append({'number': n, 'amount': amt})

            else:
                for tok in raw_tokens:
                    expanded = expand_token(tok)
                    for n in expanded:
                        if len(n) <= 2 and n.isdigit():
                            num_formatted = n.zfill(2)
                            result[active_category].append({'number': num_formatted, 'amount': amt})

    # Tính tổng tóm tắt
    sum_de = sum(item['amount'] for item in result['de'])
    sum_lo = sum(item['amount'] for item in result['lo'])
    sum_3c = sum(item['amount'] for item in result['bacang'])
    sum_x2 = sum(item['amount'] for item in result['xien2'])
    sum_x3 = sum(item['amount'] for item in result['xien3'])
    sum_x4 = sum(item['amount'] for item in result['xien4'])
    sum_xien = sum_x2 + sum_x3 + sum_x4

    summary = {
        'de_count': len(result['de']),
        'de_sum': sum_de,
        'lo_count': len(result['lo']),
        'lo_sum': sum_lo,
        'bacang_count': len(result['bacang']),
        'bacang_sum': sum_3c,
        'xien_count': len(result['xien2']) + len(result['xien3']) + len(result['xien4']),
        'xien_sum': sum_xien,
    }
    result['summary'] = summary
    return result
