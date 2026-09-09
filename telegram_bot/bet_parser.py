import os
import re
import sys
import unicodedata
from itertools import combinations

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Bảng từ viết tắt (shorthands) đồng bộ 100% với index.html
SHORTHANDS = {
    "k": "00.11.22.33.44.55.66.77.88.99",
    "kep": "00.11.22.33.44.55.66.77.88.99",
    "lip": "00.11.22.33.44.55.66.77.88.99",
    "kl": "050.161.272.383.494",
    "apkep": "010.121.232.343.454.565.676.787.898",
    "kepam": "070.141.292.363.585",
    "t0": "00.19.28.37.46.55.64.73.82.91",
    "t1": "01.29.38.47.56.65.74.83.92.10",
    "t2": "02.11.20.39.48.57.66.75.84.93",
    "t3": "03.121.30.49.58.676.85.94",
    "t4": "04.13.22.31.40.59.68.77.86.95",
    "t5": "05.14.232.41.50.69.787.96",
    "t6": "06.15.24.33.42.51.60.79.88.97",
    "t7": "07.16.25.343.52.61.70.89.98",
    "t8": "08.17.26.35.44.53.62.71.80.99",
    "t9": "09.18.27.36.454.63.72.81.90",
    "cham0": "00.010.020.030.040.050.060.070.080.090",
    "cham1": "101.11.121.131.141.151.161.171.181.191",
    "cham2": "202.212.22.232.242.252.262.272.282.292",
    "cham3": "303.313.323.33.343.353.363.373.383.393",
    "cham4": "404.414.424.434.44.454.464.474.484.494",
    "cham5": "505.515.525.535.545.55.565.575.585.595",
    "cham6": "606.616.626.636.646.656.66.676.686.696",
    "cham7": "707.717.727.737.747.757.767.77.787.797",
    "cham8": "808.818.828.838.848.858.868.878.88.898",
    "cham9": "909.919.929.939.949.959.969.979.989.99",
    "cc": "00.22.44.66.88.02.20.04.40.06.60.08.80.242.262.282.464.484.686",
    "cl": "01.03.05.07.09.21.23.25.27.29.41.43.45.47.49.61.63.65.67.69.81.83.85.87.89",
    "lc": "10.12.14.16.18.30.32.34.36.38.50.52.54.56.58.70.72.74.76.78.90.92.94.96.98",
    "ll": "11.33.55.77.99.131.151.171.191.353.373.393.575.595.797",
    "d0": "00.01.02.03.04.05.06.07.08.09",
    "d1": "10.11.12.13.14.15.16.17.18.19",
    "d2": "20.21.22.23.24.25.26.27.28.29",
    "d3": "30.31.32.33.34.35.36.37.38.39",
    "d4": "40.41.42.43.44.45.46.47.48.49",
    "d5": "50.51.52.53.54.55.56.57.58.59",
    "d6": "60.61.62.63.64.65.66.67.68.69",
    "d7": "70.71.72.73.74.75.76.77.78.79",
    "d8": "80.81.82.83.84.85.86.87.88.89",
    "d9": "90.91.92.93.94.95.96.97.98.99",
    "d'0": "00.10.20.30.40.50.60.70.80.90",
    "d'1": "01.11.21.31.41.51.61.71.81.91",
    "d'2": "02.12.22.32.42.52.62.72.82.92",
    "d'3": "03.13.23.33.43.53.63.73.83.93",
    "d'4": "04.14.24.34.44.54.64.74.84.94",
    "d'5": "05.15.25.35.45.55.65.75.85.95",
    "d'6": "06.16.26.36.46.56.66.76.86.96",
    "d'7": "07.17.27.37.47.57.67.77.87.97",
    "d'8": "08.18.28.38.48.58.68.78.88.98",
    "d'9": "09.19.29.39.49.59.69.79.89.99",
    "h05": "050.55.00", "h50": "050.55.00", "h55": "050.55.00", "h00": "050.55.00",
    "h16": "161.66.11", "h61": "161.66.11", "h66": "161.66.11", "h11": "161.66.11",
    "h27": "272.22.77", "h72": "272.22.77", "h22": "272.22.77", "h77": "272.22.77",
    "h38": "383.33.88", "h83": "383.33.88", "h33": "383.33.88", "h88": "383.33.88",
    "h49": "494.44.99", "h94": "494.44.99", "h44": "494.44.99", "h99": "494.44.99",
    "h01": "01.10.06.60.51.15.56.65", "h10": "01.10.06.60.51.15.56.65", "h06": "01.10.06.60.51.15.56.65", "h60": "01.10.06.60.51.15.56.65",
    "h15": "01.10.06.60.51.15.56.65", "h51": "01.10.06.60.51.15.56.65", "h56": "01.10.06.60.51.15.56.65", "h65": "01.10.06.60.51.15.56.65",
    "h02": "02.20.07.70.52.25.57.75", "h20": "02.20.07.70.52.25.57.75", "h07": "02.20.07.70.52.25.57.75", "h70": "02.20.07.70.52.25.57.75",
    "h52": "02.20.07.70.52.25.57.75", "h25": "02.20.07.70.52.25.57.75", "h57": "02.20.07.70.52.25.57.75", "h75": "02.20.07.70.52.25.57.75",
    "h03": "03.30.08.80.53.35.58.85", "h30": "03.30.08.80.53.35.58.85", "h08": "03.30.08.80.53.35.58.85", "h80": "03.30.08.80.53.35.58.85",
    "h53": "03.30.08.80.53.35.58.85", "h35": "03.30.08.80.53.35.58.85", "h58": "03.30.08.80.53.35.58.85", "h85": "03.30.08.80.53.35.58.85",
    "h04": "04.40.09.90.54.45.59.95", "h40": "04.40.09.90.54.45.59.95", "h09": "04.40.09.90.54.45.59.95", "h90": "04.40.09.90.54.45.59.95",
    "h54": "04.40.09.90.54.45.59.95", "h45": "04.40.09.90.54.45.59.95", "h59": "04.40.09.90.54.45.59.95", "h95": "04.40.09.90.54.45.59.95",
    "h12": "121.171.626.676", "h21": "121.171.626.676", "h17": "121.171.626.676", "h71": "121.171.626.676",
    "h62": "121.171.626.676", "h26": "121.171.626.676", "h67": "121.171.626.676", "h76": "121.171.626.676",
    "h13": "131.181.636.686", "h31": "131.181.636.686", "h18": "131.181.636.686", "h81": "131.181.636.686",
    "h63": "131.181.636.686", "h36": "131.181.636.686", "h68": "131.181.636.686", "h86": "131.181.636.686",
    "h14": "141.191.646.696", "h41": "141.191.646.696", "h19": "141.191.646.696", "h91": "141.191.646.696",
    "h64": "141.191.646.696", "h46": "141.191.646.696", "h69": "141.191.646.696", "h96": "141.191.646.696",
    "h23": "232.282.737.787", "h32": "232.282.737.787", "h28": "232.282.737.787", "h82": "232.282.737.787",
    "h73": "232.282.737.787", "h37": "232.282.737.787", "h78": "232.282.737.787", "h87": "232.282.737.787",
    "h24": "242.292.747.797", "h42": "242.292.747.797", "h29": "242.292.747.797", "h92": "242.292.747.797",
    "h74": "242.292.747.797", "h47": "242.292.747.797", "h79": "242.292.747.797", "h97": "242.292.747.797",
    "h34": "343.393.848.898", "h43": "343.393.848.898", "h39": "343.393.848.898", "h93": "343.393.848.898",
    "h84": "343.393.848.898", "h48": "343.393.848.898", "h89": "343.393.848.898", "h98": "343.393.848.898",
    "tongto": "05.06.07.08.09.14.15.16.17.18.23.24.25.26.27.32.33.34.35.36.41.42.43.44.45.50.51.52.53.54.60.61.62.63.69.70.71.72.78.79.80.81.87.88.89.90.96.97.98.99",
    "tongbe": "00.01.02.03.04.10.11.12.13.19.20.21.22.28.29.30.31.37.38.39.40.46.47.48.49.55.56.57.58.59.64.65.66.67.68.73.74.75.76.77.82.83.84.85.86.91.92.93.94.95",
    "tongle": "01.03.05.07.09.10.12.14.16.18.21.23.25.27.29.30.32.34.36.38.41.43.45.47.49.50.52.54.56.58.61.63.65.67.69.70.72.74.76.78.81.83.85.87.89.90.92.94.96.98",
    "tongchan": "00.02.04.06.08.11.13.15.17.19.20.22.24.26.28.31.33.35.37.39.40.42.44.46.48.51.53.55.57.59.60.62.64.66.68.71.73.75.77.79.80.82.84.86.88.91.93.95.97.99",
    "toto": "55.66.77.88.99.565.575.585.595.676.686.696.787.797.898",
    "tobe": "90.91.92.93.94.80.81.82.83.84.70.71.72.73.74.60.61.62.63.64.50.51.52.53.54",
    "beto": "05.06.07.08.09.15.16.17.18.19.25.26.27.28.29.35.36.37.38.39.45.46.47.48.49",
    "bebe": "00.11.22.33.44.01.10.02.20.03.30.04.40.121.131.141.232.242.343",
    "dauto": "50.51.52.53.54.55.56.57.58.59.60.61.62.63.64.65.66.67.68.69.70.71.72.73.74.75.76.77.78.79.80.81.82.83.84.85.86.87.88.89.90.91.92.93.94.95.96.97.98.99",
    "daube": "00.01.02.03.04.05.06.07.08.09.10.11.12.13.14.15.16.17.18.19.20.21.22.23.24.25.26.27.28.29.30.31.32.33.34.35.36.37.38.39.40.41.42.43.44.45.46.47.48.49",
    "daule": "10.11.12.13.14.15.16.17.18.19.30.31.32.33.34.35.36.37.38.39.50.51.52.53.54.55.56.57.58.59.70.71.72.73.74.75.76.77.78.79.90.91.92.93.94.95.96.97.98.99",
    "dauchan": "00.01.02.03.04.05.06.07.08.09.20.21.22.23.24.25.26.27.28.29.40.41.42.43.44.45.46.47.48.49.60.61.62.63.64.65.66.67.68.69.80.81.82.83.84.85.86.87.88.89",
    "ditchan": "00.02.04.06.08.10.12.14.16.18.20.22.24.26.28.30.32.34.36.38.40.42.44.46.48.50.52.54.56.58.60.62.64.66.68.70.72.74.76.78.80.82.84.86.88.90.92.94.96.98",
    "ditle": "01.03.05.07.09.11.13.15.17.19.21.23.25.27.29.31.33.35.37.39.41.43.45.47.49.51.53.55.57.59.61.63.65.67.69.71.73.75.77.79.81.83.85.87.89.91.93.95.97.99",
    "ditbe": "00.01.02.03.04.10.11.12.13.14.20.21.22.23.24.30.31.32.33.34.40.41.42.43.44.50.51.52.53.54.60.61.62.63.64.70.71.72.73.74.80.81.82.83.84.90.91.92.93.94",
    "ditto": "05.06.07.08.09.15.16.17.18.19.25.26.27.28.29.35.36.37.38.39.45.46.47.48.49.55.56.57.58.59.65.66.67.68.69.75.76.77.78.79.85.86.87.88.89.95.96.97.98.99"
}

def load_shorthands_from_file(file_path: str = None) -> bool:
    """Nạp động các từ viết tắt từ file nhaptat.txt"""
    candidates = [file_path] if file_path else [
        os.path.join(os.path.dirname(__file__), "..", "nhaptat.txt"),
        os.path.join(os.path.dirname(__file__), "nhaptat.txt"),
        r"c:\inetpub\wwwroot\lk\nhaptat.txt"
    ]
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    count = 0
                    for line in f:
                        line = line.strip()
                        if not line or "=" not in line or line.startswith("#"):
                            continue
                        parts = line.split("=", 1)
                        k = parts[0].strip().lower()
                        v = parts[1].strip()
                        if k and v:
                            SHORTHANDS[k] = v
                            count += 1
                return True
            except Exception:
                pass
    return False

# Tự động nạp khi module khởi động
load_shorthands_from_file()

def reload_shorthands() -> int:
    load_shorthands_from_file()
    return len(SHORTHANDS)

def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = re.sub(r'[\u0300-\u036f]', '', s)
    return s

def normalize_bet_line(line: str) -> str:
    norm = strip_accents(line.strip().lower())
    norm = re.sub(r'^(xienquay|xq|xienq|quay|q|xquay|lo\s*xien|l\s*xien|de|lo|xien|x|x2|x3|x4|3cang|3c|d|l|c|bc|cang|3\s*cang|bo|he)[.:;\-]+\s*', r'\1 ', norm, flags=re.I)
    norm = re.sub(r'([dđ])`', r'\1', norm)
    norm = re.sub(r'[’‘ʼ＇]', "'", norm)
    norm = re.sub(r'(\d)\s*,\s*(\d)', r'\1.\2', norm)
    norm = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', norm)
    norm = re.sub(r'\b(?:chan\s+chan|chanchan)\b', 'chanchan', norm)
    norm = re.sub(r'\b(?:chan\s+le|chanle)\b', 'chanle', norm)
    norm = re.sub(r'\b(?:le\s+chan|lechan)\b', 'lechan', norm)
    norm = re.sub(r'\b(?:le\s+le|lele)\b', 'lele', norm)
    norm = re.sub(r'\b(?:to\s+to|toto)\b', 'toto', norm)
    norm = re.sub(r'\b(?:to\s+be|tobe)\b', 'tobe', norm)
    norm = re.sub(r'\b(?:be\s+to|beto)\b', 'beto', norm)
    norm = re.sub(r'\b(?:be\s+be|bebe)\b', 'bebe', norm)
    norm = re.sub(r'\b(?:dau\s+to|dauto)\b', 'dauto', norm)
    norm = re.sub(r'\b(?:dau\s+be|daube)\b', 'daube', norm)
    norm = re.sub(r'\b(?:dau\s+chan|dauchan)\b', 'dauchan', norm)
    norm = re.sub(r'\b(?:dau\s+le|daule)\b', 'daule', norm)
    norm = re.sub(r'\b(?:dit\s+to|duoi\s+to|ditto)\b', 'ditto', norm)
    norm = re.sub(r'\b(?:dit\s+be|duoi\s+be|ditbe)\b', 'ditbe', norm)
    norm = re.sub(r'\b(?:dit\s+chan|duoi\s+chan|ditchan)\b', 'ditchan', norm)
    norm = re.sub(r'\b(?:dit\s+le|duoi\s+le|ditle)\b', 'ditle', norm)
    norm = re.sub(r'\b(?:tong\s+to|tongto)\b', 'tongto', norm)
    norm = re.sub(r'\b(?:tong\s+be|tongbe)\b', 'tongbe', norm)
    norm = re.sub(r'\b(?:tong\s+chan|tongchan)\b', 'tongchan', norm)
    norm = re.sub(r'\b(?:tong\s+le|tongle)\b', 'tongle', norm)
    norm = re.sub(r'\b(?:daudit|dau\s+dit|dau\s+duoi|dd|đđ)\s*(\d)(?![0-9])', r'daudit\1', norm)
    norm = re.sub(r'\b(?:sat\s*kep|satkep|ap\s*kep|apkep)\b', 'apkep', norm)
    norm = re.sub(r'\b(?:kep\s*am|kepam)\b', 'kepam', norm)
    norm = re.sub(r'\b(?:kep\s*lech|keplech)\b', 'kl', norm)
    norm = re.sub(r'\b(?:kep\s*bang|kepbang|lip|kep)(?=[x=+*\d\s]|$)', 'k ', norm)
    norm = re.sub(r'\b(?:dau)\s*(\d)(?![0-9])', r'dau\1', norm)
    norm = re.sub(r'\b(?:dit|duoi)\s*(\d)(?![0-9])', r'dit\1', norm)
    norm = re.sub(r'\b(?:cham)\s*(\d)(?![0-9])', r'cham\1', norm)
    norm = re.sub(r'\b(?:c)\s*(\d)(?![0-9])', r'cham\1', norm)
    norm = re.sub(r'\b(?:tong)\s*(\d)(?![0-9])', r't\1', norm)
    norm = re.sub(r'\b(?:d)\s*(\d)(?![0-9])', r'd\1', norm)
    norm = re.sub(r'\b(?:t)\s*(\d)(?![0-9])', r't\1', norm)
    norm = re.sub(r'\b(?:bo|he)\s*(\d{1,2})(?![0-9])', r'bo\1', norm)
    norm = re.sub(r'\b(?:moi\s*con\s*bang|moi\s*con)\b', 'mc', norm)
    norm = re.sub(r'\b(?:3\s*cang|3\s*c)\b', '3c', norm)
    norm = re.sub(r'\b(?:xien\s*quay|x\s*quay)\b', 'xq', norm)
    norm = re.sub(r'\b(?:lo\s*xien|l\s*xien)\b', 'xien', norm)
    norm = re.sub(r'((?:mỗi\s*con\s*=?|moi\s*con\s*=?|mc\s*=|mc|=\s*mc|=|\+|\*|x)\s*\d+(?:\.\d+)?)\s*[\.,;](?=[\s.,;\-\d]|$)', r'\1 ', norm, flags=re.I)
    norm = re.sub(r'\s*[,.]\s*(?==|x|mc|moi\s*con|\+|\*)', ' ', norm)
    def _repl_sep(m):
        prefix = m.group(1)
        raw_op = m.group(2).lower()
        amt = m.group(3)
        op = '=' if '=' in raw_op else 'x'
        return f"{prefix}{op}{amt}"
    norm = re.sub(r'([a-z0-9])\s*(mỗi\s*con\s*=?|moi\s*con\s*=?|mc\s*=|mc|=\s*mc|=|\+|\*|x)\s*(\d+(?:\.\d+)?)(?:\s*(?:d|đ|₫|k|n)(?=[\s.,;\-\d]|$))?(?![a-z0-9])', _repl_sep, norm, flags=re.I)
    norm = re.sub(r'\bmc\s*(\d+(?:\.\d+)?)(?:\s*(?:d|đ|₫|k|n)(?=[\s.,;\-\d]|$))?(?![a-z0-9])', r'=\1', norm, flags=re.I)
    norm = re.sub(r'\s+(\d+(?:\.\d+)?)\s*(?:d|đ|₫|k|n)(?![a-z0-9])', r' =\1', norm, flags=re.I)
    return norm

def normalize_shorthand_token(token: str) -> str:
    t = token.strip().lower().replace("’", "'").replace("‘", "'")
    m_dd = re.match(r'^daudit(\d)$', t)
    if m_dd:
        d = m_dd.group(1)
        dau_k = 'd' + d
        dit_k = "d'" + d
        if dau_k in SHORTHANDS and dit_k in SHORTHANDS:
            return SHORTHANDS[dau_k] + '.' + SHORTHANDS[dit_k]
    m_c = re.match(r'^c(\d)$', t)
    if m_c:
        return 'cham' + m_c.group(1)
    m_dau = re.match(r'^dau(\d)$', t)
    if m_dau:
        return 'd' + m_dau.group(1)
    m_dit = re.match(r'^dit(\d)$', t)
    if m_dit:
        return "d'" + m_dit.group(1)
    m_he = re.match(r'^(he|bo)(\d{1,2})$', t)
    if m_he:
        num = m_he.group(2)
        if len(num) == 1:
            num = '0' + num
        return 'h' + num
    aliases = {
        'chanle': 'cl', 'lechan': 'lc', 'chanchan': 'cc', 'lele': 'll',
        'dc': 'dauchan', 'dl': 'daule', 'dt': 'dauto', 'db': 'daube',
        'tb': 'tobe', 'tt': 'toto', 'bt': 'beto', 'bb': 'bebe',
        'tc': 'tongchan', 'tl': 'tongle', 'chan': 'ditchan', 'le': 'ditle',
        'lip': 'k', 'kep': 'k'
    }
    return aliases.get(t, t)

def resolve_shorthands(numbers_str: str) -> str:
    s = numbers_str
    # Mở rộng dau0,8 -> dau0, dau8 hoặc c1,2,3 -> cham1, cham2, cham3
    def expand_prefix_multi(m):
        raw_prefix = m.group(1).lower()
        prefix = "cham" if raw_prefix == "c" else raw_prefix
        first = m.group(2)
        rest = m.group(3)
        digits = [d for d in re.split(r'[^0-9]+', rest) if d]
        expanded = [prefix + first] + [prefix + d for d in digits]
        return ','.join(expanded)

    combined_regex = re.compile(r'(dau|dit|daudit|d\'|d|he|bo|t|cham|c)([0-9]{1,2})((?:[.,\-\s]+[0-9]{1,2})+)', re.I)
    s = combined_regex.sub(expand_prefix_multi, s)

    parts = re.split(r'([.,\-\s]+)', s)
    resolved = []
    for part in parts:
        trimmed = part.strip().lower()
        if not trimmed:
            resolved.append(part)
            continue
        norm = normalize_shorthand_token(part)
        if norm in SHORTHANDS:
            resolved.append(SHORTHANDS[norm])
        elif re.match(r'^[\d.]+$', norm):
            resolved.append(norm)
        else:
            resolved.append(part)
    return ''.join(resolved)


def parse_numbers_from_bet_string(numbers_str: str, bet_type: str) -> list[str]:
    segments = [s for s in re.split(r'[.,\-\s]+', numbers_str) if s]
    numbers = []
    for seg in segments:
        if not seg.isdigit():
            continue
        if bet_type in ('de', 'lo'):
            if len(seg) == 2:
                numbers.append(seg)
            elif len(seg) == 3 and seg[0] == seg[2]:
                numbers.append(seg[0:2])
                numbers.append(seg[1:3])
        elif bet_type in ('xien', 'xienquay'):
            if len(seg) == 2:
                numbers.append(seg)
            elif len(seg) == 3 and seg[0] == seg[2]:
                numbers.append(seg[0:2])
                numbers.append(seg[1:3])
        elif bet_type == 'bacang':
            if len(seg) == 3:
                numbers.append(seg)
    return numbers

# Horizontal whitespace (spaces, tabs) but NOT newline
HSPACE = r'[^\S\r\n]'

CAT_MAP = {
    'lo': 'lo', 'lô': 'lo', 'l': 'lo',
    'de': 'de', 'đề': 'de', 'đê': 'de', 'đe': 'de', 'dê': 'de', 'dè': 'de', 'đè': 'de', 'd': 'de', 'đ': 'de',
    'xien': 'xien', 'xiên': 'xien',
    'xienquay': 'xienquay', 'xq': 'xienquay', 'xienq': 'xienquay', 'quay': 'xienquay', 'xquay': 'xienquay',
    '3cang': 'bacang', '3càng': 'bacang', '3c': 'bacang', 'bc': 'bacang'
}

cat_choices = r'lô|lo|l|đề|de|đê|đe|dê|dè|đè|đ|d|xienquay|xq|xienq|quay|xquay|xien|xiên|3cang|3càng|3c|bc'

multi_bet_regex = re.compile(
    r'(?i)(?P<prefix>^|[\r\n;,]|' + HSPACE + r'+)'
    r'(?P<cat1>' + cat_choices + r')'
    r'(?:' + HSPACE + r'+|[.:;\-])'
    r'(?P<nums>[0-9.,\-\s_a-zA-Z\'’‘＇`]+?)'
    r'\s*(?P<op1>x|\*|=|\+|mc|mỗi\s*con\s*=?|moi\s*con\s*=?)\s*'
    r'(?P<amt1>\d+(?:\.\d+)?(?:k|d|n|đ|₫)?)\.?'
    r'(?P<rest>(?:' + HSPACE + r'*[,;.:\-]?' + HSPACE + r'*(?:' + cat_choices + r')(?:' + HSPACE + r'*(?:x|\*|=|\+|mc|mỗi\s*con\s*=?|moi\s*con\s*=?)\s*|' + HSPACE + r'+)\d+(?:\.\d+)?(?:k|d|n|đ|₫)?)+)'
    r'(?=[.,;:' + HSPACE + r']|$)',
    re.MULTILINE
)

sub_clause_regex = re.compile(
    r'(?i)' + HSPACE + r'*[,;.:\-]?' + HSPACE + r'*'
    r'(?P<cat>' + cat_choices + r')'
    r'(?:' + HSPACE + r'*(?P<op>x|\*|=|\+|mc|mỗi\s*con\s*=?|moi\s*con\s*=?)\s*|' + HSPACE + r'+)'
    r'(?P<amt>\d+(?:\.\d+)?(?:k|d|n|đ|₫)?)'
)

def expand_multi_bets(text: str) -> str:
    def repl(m):
        prefix = m.group('prefix')
        cat1_raw = m.group('cat1').strip().lower()
        nums = m.group('nums').strip()
        op1 = m.group('op1').strip()
        amt1 = m.group('amt1').strip()
        rest = m.group('rest')

        c1 = CAT_MAP.get(cat1_raw, cat1_raw)
        lines = [f"{c1} {nums}{op1}{amt1}"]

        for sub_m in sub_clause_regex.finditer(rest):
            cat_k_raw = sub_m.group('cat').strip().lower()
            op_k = sub_m.group('op')
            op_str = op_k.strip() if op_k else 'x'
            amt_k = sub_m.group('amt').strip()
            c_k = CAT_MAP.get(cat_k_raw, cat_k_raw)
            lines.append(f"{c_k} {nums}{op_str}{amt_k}")

        p = '\n' if (prefix and ('\n' in prefix or '\r' in prefix)) else (' ' if prefix else '')
        return f"{p}" + "\n".join(lines) + "\n"

    return multi_bet_regex.sub(repl, text)

def parse_combined_input(input_str: str):
    processed = input_str.replace("’", "'").replace("‘", "'").replace("ʼ", "'").replace("＇", "'").replace("`", "'")
    # Chuẩn hóa 3 càng trước để tránh bị tách số 3 thành dòng riêng
    processed = re.sub(r'\b(?:3\s*cang|3\s*càng|ba\s*cang|ba\s*càng|3\s*c)\b', '3cang', processed, flags=re.I)
    
    # 1. Tách từ khóa đứng liền số: de88 -> de 88
    keyword_regex = re.compile(
        r'(^|[\s,;.])(xienquay|xquay|xienq|3cang|3càng|3\s*càng|3\s*cang|quay|xien|xiên|xq|de|đề|đê|đe|dê|dè|đè|Dè|Đè|dề|Dề|lo|lô|3c|bc|càng|cang|d(?!\d(?!\d))|đ(?!\d(?!\d))|l|c|q|x)(\d)',
        re.I
    )
    processed = keyword_regex.sub(r'\1\2 \3', processed)

    # 1.1 Mở rộng cú pháp cược đa thể loại cùng dãy số: vd l 101.52.23x10 dex50 xienx20
    processed = expand_multi_bets(processed)

    # 2. Tách dòng nếu có danh mục mới trên cùng một dòng
    same_line_pattern = re.compile(
        r'[\s,;.:\-]+(xienquay|xq|xienq|quay|q|xquay|lô\s*xiên|lo\s*xien|đề|de|đê|đe|dê|dè|đè|Dè|Đè|dề|Dề|lô|lo|l|xiên|xien|x2|x3|x4)(?=[\s.,;\-\d:]|$)|[\s,;.:\-]+(3càng|3cang|3\s*càng|3\s*cang|3c|bc|càng|cang)(?=[\s.,;\-\d:]|$)|[\s,;.:\-]+(d|đ|l|c)(?=[\s.,;\-\d:]|$)(?=\s+[a-z0-9\'’‘＇`])',
        re.I
    )
    def same_line_repl(match):
        m = match.group(0)
        return "\n" + re.sub(r'^[\s,;.:\-]+', '', m).strip()
    processed = same_line_pattern.sub(same_line_repl, processed)

    # 3. Tách dòng sau mỗi cụm cược: 464=10. -> tách dòng!
    bet_split_regex = re.compile(
        r'([a-z0-9])\s*((?:mỗi\s*con\s*=?|moi\s*con\s*=?|mc\s*=|mc|=\s*mc|=|\+|\*|x)\s*\d+(?:\.\d+)?(?:\s*(?:d|đ|₫|k|n)(?=[\s.,;\-\d]|$))?)\b[\s.,;\-]+(?=[^\s.,;\-])',
        re.I
    )
    processed = bet_split_regex.sub(r'\1 \2\n', processed)

    lines = processed.split('\n')
    parsed_bets = {
        'de': [],
        'lo': [],
        'xien2': [],
        'xien3': [],
        'xien4': [],
        'bacang': [],
        'invalid_items': [],
        'rawOrder': []
    }

    type_regex = {
        'de': re.compile(r'^(de|d)$', re.I),
        'lo': re.compile(r'^(lo|l)$', re.I),
        'xienquay': re.compile(r'^(xienquay|xq|xienq|quay|q|xquay)$', re.I),
        'xien': re.compile(r'^(lo\s*xien|xien|x|x2|x3|x4)$', re.I),
        'bacang': re.compile(r'^(3cang|3c|c|bc|cang|3\s*cang)$', re.I)
    }

    active_category = 'de'

    for line in lines:
        orig_line = line.strip()
        if not orig_line:
            continue

        trimmed = normalize_bet_line(orig_line)
        line_type = ''
        m_space = re.match(
            r'^(xienquay|xq|xienq|quay|q|xquay|lo\s*xien|l\s*xien|de|lo|xien|x2|x3|x4|3cang|3c|d|l|c|bc|cang|3\s*cang|bo|he)(?:\b|[.:;\-\s])\s*(.*)$',
            trimmed,
            re.I
        )
        if m_space:
            raw_t = m_space.group(1).lower()
            if type_regex['de'].match(raw_t): line_type = 'de'
            elif type_regex['lo'].match(raw_t): line_type = 'lo'
            elif type_regex['xienquay'].match(raw_t): line_type = 'xienquay'
            elif type_regex['xien'].match(raw_t): line_type = 'xien'
            elif type_regex['bacang'].match(raw_t): line_type = 'bacang'

            if line_type:
                active_category = line_type
                trimmed = re.sub(r'^[.:;\-\s]+', '', m_space.group(2)).strip()
                if not trimmed:
                    continue

        if not line_type:
            line_type = active_category

        # Tìm các cụm cược: [các số] [x / + / * / =] [tiền]
        group_regex = re.compile(r'([a-wy-z0-9\'’‘＇`.,\-\s]+)([x=+*])(\d+(?:\.\d+)?)', re.I)
        matches = list(group_regex.finditer(trimmed))

        if not matches:
            # Dòng không có cược hợp lệ -> ghi nhận trả lại (nếu không phải là header rỗng)
            cleaned_check = re.sub(r'^[.:;\-\s]+', '', orig_line).strip()
            if cleaned_check and not re.match(r'^(de|đề|lo|lô|xien|xiên|3cang|3c|bc)$', cleaned_check, re.I):
                parsed_bets['invalid_items'].append(orig_line)
            continue

        for g_match in matches:
            raw_num_str = g_match.group(1).strip()
            raw_op = g_match.group(2)
            op = '=' if raw_op in ('=', '+', '*') else 'x'
            amt = float(g_match.group(3))
            if amt <= 0:
                continue

            amt_str = f"{int(amt) if amt == int(amt) else amt}"
            num_str = resolve_shorthands(raw_num_str)
            raw_tokens = [s for s in re.split(r'[.,\-\s]+', num_str) if s]

            valid_numbers_for_group = []
            for tok in raw_tokens:
                if not tok.isdigit():
                    # Token không phải là chữ số hợp lệ
                    parsed_bets['invalid_items'].append(tok)
                    continue

                if line_type in ('de', 'lo', 'xien', 'xienquay'):
                    if len(tok) == 2:
                        valid_numbers_for_group.append(tok)
                    elif len(tok) == 3:
                        if tok[0] == tok[2]:
                            # Số đối xứng ví dụ 686 -> 68, 86 hoặc 585 -> 58, 85
                            valid_numbers_for_group.append(tok[0:2])
                            valid_numbers_for_group.append(tok[1:3])
                        else:
                            # 3 số khác nhau (như 685) chỉ có 3 càng mới nhận, đề và lô không nhận -> trả lại!
                            parsed_bets['invalid_items'].append(tok)
                    else:
                        # 1 số hoặc >= 4 số -> không hợp lệ trong đề/lô
                        parsed_bets['invalid_items'].append(tok)

                elif line_type == 'bacang':
                    if len(tok) == 3:
                        valid_numbers_for_group.append(tok)
                    else:
                        # 3 càng chỉ nhận số có đúng 3 chữ số
                        parsed_bets['invalid_items'].append(tok)

            if not valid_numbers_for_group:
                continue

            if line_type == 'xien':
                if 2 <= len(valid_numbers_for_group) <= 4:
                    parsed_bets[f'xien{len(valid_numbers_for_group)}'].append({'numbers': valid_numbers_for_group, 'amount': amt})
                else:
                    parsed_bets['invalid_items'].append('.'.join(valid_numbers_for_group))
            elif line_type == 'xienquay':
                if len(valid_numbers_for_group) >= 2:
                    for k in (2, 3, 4):
                        if len(valid_numbers_for_group) >= k:
                            for combo in combinations(valid_numbers_for_group, k):
                                parsed_bets[f'xien{k}'].append({'numbers': list(combo), 'amount': amt})
            else:
                for n in valid_numbers_for_group:
                    parsed_bets[line_type].append({'number': n, 'amount': amt})

    return parsed_bets


def parse_bet_message(raw_text: str) -> dict:
    res = parse_combined_input(raw_text)
    
    sum_de = sum(x['amount'] for x in res['de'])
    sum_lo = sum(x['amount'] for x in res['lo'])
    sum_3c = sum(x['amount'] for x in res['bacang'])
    sum_x2 = sum(x['amount'] for x in res['xien2'])
    sum_x3 = sum(x['amount'] for x in res['xien3'])
    sum_x4 = sum(x['amount'] for x in res['xien4'])
    sum_xien = sum_x2 + sum_x3 + sum_x4

    res['summary'] = {
        'de_count': len(res['de']),
        'de_sum': sum_de,
        'lo_count': len(res['lo']),
        'lo_sum': sum_lo,
        'bacang_count': len(res['bacang']),
        'bacang_sum': sum_3c,
        'xien_count': len(res['xien2']) + len(res['xien3']) + len(res['xien4']),
        'xien_sum': sum_xien,
    }
    return res


def format_ok_receipt(parsed: dict, msg_index: int = 1) -> str:
    """
    Định dạng tin nhắn xác nhận cho khách theo đúng chuẩn người dùng yêu cầu:
    Ok tin 1
    (Nếu có số lỗi không hiểu: Trả lại ...)
    Không liệt kê các con cược nữa.
    """
    lines = [f"Ok tin {msg_index}"]

    invalid_items = parsed.get('invalid_items', [])
    if invalid_items:
        unique_inv = list(dict.fromkeys(invalid_items))
        joined_inv = " ".join(unique_inv) if all(x.isdigit() for x in unique_inv) else ", ".join(unique_inv)
        lines.append(f"Trả lại {joined_inv}")

    return "\n".join(lines)


def format_ok_receipt_detailed(parsed: dict, msg_index: int = 1) -> str:
    """Phiên bản liệt kê chi tiết (dự phòng khi cần tra cứu)"""
    lines = [f"Ok tin {msg_index}"]

    if parsed.get('de'):
        de_sums = {}
        for b in parsed['de']:
            de_sums[b['number']] = de_sums.get(b['number'], 0) + b['amount']
        groups = {}
        for num, amt in de_sums.items():
            amt_r = int(amt) if amt.is_integer() else amt
            groups.setdefault(amt_r, []).append(num)
        parts = []
        for amt in sorted(groups.keys()):
            nums = sorted(groups[amt], key=lambda x: int(x) if x.isdigit() else x)
            parts.append(".".join(nums) + f"x{amt}")
        lines.append("Đề " + ", ".join(parts))

    if parsed.get('lo'):
        lo_sums = {}
        for b in parsed['lo']:
            lo_sums[b['number']] = lo_sums.get(b['number'], 0) + b['amount']
        groups = {}
        for num, amt in lo_sums.items():
            amt_r = int(amt) if amt.is_integer() else amt
            groups.setdefault(amt_r, []).append(num)
        parts = []
        for amt in sorted(groups.keys()):
            nums = sorted(groups[amt], key=lambda x: int(x) if x.isdigit() else x)
            parts.append(".".join(nums) + f"x{amt}")
        lines.append("Lô " + ", ".join(parts))

    if parsed.get('bacang'):
        bc_sums = {}
        for b in parsed['bacang']:
            bc_sums[b['number']] = bc_sums.get(b['number'], 0) + b['amount']
        groups = {}
        for num, amt in bc_sums.items():
            amt_r = int(amt) if amt.is_integer() else amt
            groups.setdefault(amt_r, []).append(num)
        parts = []
        for amt in sorted(groups.keys()):
            nums = sorted(groups[amt], key=lambda x: int(x) if x.isdigit() else x)
            parts.append(".".join(nums) + f"x{amt}")
        lines.append("3c " + ", ".join(parts))

    all_xien = []
    for k in ('xien2', 'xien3', 'xien4'):
        all_xien.extend(parsed.get(k, []))
    if all_xien:
        x_sums = {}
        for b in all_xien:
            k_str = "-".join(b['numbers'])
            x_sums[k_str] = x_sums.get(k_str, 0) + b['amount']
        groups = {}
        for pair, amt in x_sums.items():
            amt_r = int(amt) if amt.is_integer() else amt
            groups.setdefault(amt_r, []).append(pair)
        parts = []
        for amt in sorted(groups.keys()):
            pairs = groups[amt]
            parts.append(", ".join(pairs) + f"x{amt}")
        lines.append("Xiên " + "; ".join(parts))

    invalid_items = parsed.get('invalid_items', [])
    if invalid_items:
        unique_inv = list(dict.fromkeys(invalid_items))
        joined_inv = " ".join(unique_inv) if all(x.isdigit() for x in unique_inv) else ", ".join(unique_inv)
        lines.append(f"Trả lại {joined_inv}")

    return "\n".join(lines)


