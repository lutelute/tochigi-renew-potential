"""
東京電力パワーグリッド 空容量マッピングPDFからCSVを抽出する汎用スクリプト。
栃木・千葉・茨城で同一フォーマット。

出力: capacity_transmission_lines.csv, capacity_substations.csv, capacity_distribution_substations.csv
"""
import argparse
import re
from pathlib import Path

import pandas as pd
import pdfplumber

from config import PROJECT_ROOT, get_grid_dir


def classify_page(text: str):
    if not text:
        return None
    if "配電用変電所エリア運用容量一覧表" in text:
        return "dist"
    if "運用容量一覧表" in text and "154kV" in text and "特高設備" in text:
        return "154kv"
    if "運用容量一覧表" in text and "66kV" in text and "特高設備" in text:
        return "66kv"
    if "運用容量一覧表" in text and "22kV" in text and "特高設備" in text:
        return "22kv"
    return None


def clean(val):
    if val is None:
        return ""
    return str(val).strip().replace("\n", " ").replace("\r", "")


def is_header_row(cells):
    text = " ".join(str(c) for c in cells if c)
    return any(kw in text for kw in ["送電線名", "変電所名", "送電線 No", "変電所 No",
                                      "設備容量", "空容量", "運用容量", "電圧", "一次", "二次",
                                      "当該 設備", "上位系等"])


def parse_first_col(col0):
    """'千葉県 154kV 1' → (県名, 電圧区分, No) or ('千葉県 1' → 県名, None, No)"""
    parts = col0.split()
    pref = parts[0] if parts else ""
    no = ""
    voltage_tag = ""
    for p in parts[1:]:
        if "kV" in p:
            voltage_tag = p
        elif p.isdigit():
            no = p
    return pref, voltage_tag, no


def extract_pdf(pdf_path: str, pref_name: str):
    trans_rows = []
    sub_rows = []
    dist_rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            table_type = classify_page(text)
            if table_type is None:
                continue

            tables = page.extract_tables()
            if not tables:
                continue

            for table in tables:
                if len(table) < 2:
                    continue

                # ヘッダー行を検出して種別を判定
                is_sub_table = False
                for row in table[:5]:
                    row_text = " ".join(str(c) for c in row if c)
                    if "変電所名" in row_text or "変電所 No" in row_text:
                        is_sub_table = True
                        break
                    if "送電線名" in row_text or "送電線 No" in row_text:
                        is_sub_table = False
                        break

                for row in table:
                    cells = [clean(c) for c in row]
                    if len(cells) < 5:
                        continue
                    if is_header_row(cells):
                        continue
                    # col0に"県"が含まれるデータ行のみ
                    if "県" not in cells[0]:
                        continue

                    _, vtag, no = parse_first_col(cells[0])

                    if table_type == "dist":
                        # 配電用変電所: 16 cols
                        # [県No, 変電所名, 一次kV, 二次kV, 台数, 設備容量, 運用容量, 制約要因,
                        #  空容量_当該, 空容量_上位, N1可否, N1量, 出力制御, 当該設備, 上位系設備, 備考]
                        while len(cells) < 16:
                            cells.append("")
                        dist_rows.append({
                            "県名": pref_name, "No": no, "変電所名": cells[1],
                            "電圧kV_一次": cells[2], "電圧kV_二次": cells[3],
                            "台数": cells[4], "設備容量MW": cells[5],
                            "運用容量値MW": cells[6], "運用容量制約要因": cells[7],
                            "空容量_当該設備MW": cells[8], "空容量_上位系等考慮MW": cells[9],
                            "N1電制_適用可否": cells[10], "N1電制_適用可能量MW": cells[11],
                            "平常時出力制御の可能性": cells[12], "平常時出力制御_当該設備": cells[13],
                            "平常時出力制御_上位系設備": cells[14],
                            "備考": cells[15] if len(cells) > 15 else "",
                        })
                    elif is_sub_table:
                        # 変電所: 16 cols (電圧に一次/二次がある)
                        # [県VNo, 変電所名, 一次kV, 二次kV, 台数, 設備容量, 運用容量, 制約要因,
                        #  空容量_当該, 空容量_上位, N1可否, N1量, 出力制御, 当該設備, 上位系設備, 備考]
                        v_kv = {"154kv": 154, "66kv": 66, "22kv": 22}.get(table_type, 0)
                        while len(cells) < 16:
                            cells.append("")
                        sub_rows.append({
                            "県名": pref_name, "電圧kV": v_kv, "No": no,
                            "変電所名": cells[1],
                            "電圧": f"{cells[2]}/{cells[3]}" if cells[3] else cells[2],
                            "台数": cells[4], "設備容量MW": cells[5],
                            "運用容量値MW": cells[6], "運用容量制約要因": cells[7],
                            "空容量_当該設備MW": cells[8], "空容量_上位系等考慮MW": cells[9],
                            "N1電制_適用可否": cells[10], "N1電制_適用可能量MW": cells[11],
                            "平常時出力制御の可能性": cells[12], "平常時出力制御_当該設備": cells[13],
                            "平常時出力制御_上位系設備": cells[14],
                            "備考": cells[15] if len(cells) > 15 else "",
                        })
                    else:
                        # 送電線: 15 cols
                        # [県VNo, 送電線名, 電圧, 回線数, 設備容量, 運用容量, 制約要因,
                        #  空容量_当該, 空容量_上位, N1可否, N1量, 出力制御, 当該設備, 上位系設備, 備考]
                        v_kv = {"154kv": 154, "66kv": 66, "22kv": 22}.get(table_type, 0)
                        while len(cells) < 15:
                            cells.append("")
                        trans_rows.append({
                            "県名": pref_name, "電圧kV": v_kv, "No": no,
                            "送電線名": cells[1], "電圧": cells[2], "回線数": cells[3],
                            "設備容量MW": cells[4], "運用容量値MW": cells[5],
                            "運用容量制約要因": cells[6],
                            "空容量_当該設備MW": cells[7], "空容量_上位系等考慮MW": cells[8],
                            "N1電制_適用可否": cells[9], "N1電制_適用可能量MW": cells[10],
                            "平常時出力制御の可能性": cells[11], "平常時出力制御_当該設備": cells[12],
                            "平常時出力制御_上位系設備": cells[13],
                            "備考": cells[14] if len(cells) > 14 else "",
                        })

    return trans_rows, sub_rows, dist_rows


TRANS_COLS = [
    "県名", "電圧kV", "No", "送電線名", "電圧", "回線数",
    "設備容量MW", "運用容量値MW", "運用容量制約要因",
    "空容量_当該設備MW", "空容量_上位系等考慮MW",
    "N1電制_適用可否", "N1電制_適用可能量MW",
    "平常時出力制御の可能性", "平常時出力制御_当該設備",
    "平常時出力制御_上位系設備", "備考"
]

SUB_COLS = [
    "県名", "電圧kV", "No", "変電所名", "電圧", "台数",
    "設備容量MW", "運用容量値MW", "運用容量制約要因",
    "空容量_当該設備MW", "空容量_上位系等考慮MW",
    "N1電制_適用可否", "N1電制_適用可能量MW",
    "平常時出力制御の可能性", "平常時出力制御_当該設備",
    "平常時出力制御_上位系設備", "備考"
]

DIST_COLS = [
    "県名", "No", "変電所名", "電圧kV_一次", "電圧kV_二次", "台数",
    "設備容量MW", "運用容量値MW", "運用容量制約要因",
    "空容量_当該設備MW", "空容量_上位系等考慮MW",
    "N1電制_適用可否", "N1電制_適用可能量MW",
    "平常時出力制御の可能性", "平常時出力制御_当該設備",
    "平常時出力制御_上位系設備", "備考"
]


def main():
    parser = argparse.ArgumentParser(description="空容量マッピングPDF → CSV抽出")
    parser.add_argument("--prefecture", "-p", required=True,
                        choices=["tochigi", "chiba", "ibaraki"])
    parser.add_argument("--pdf", help="PDFファイルパス")
    args = parser.parse_args()

    pref = args.prefecture
    pref_names = {"tochigi": "栃木県", "chiba": "千葉県", "ibaraki": "茨城県"}
    pref_name = pref_names[pref]

    pdf_path = args.pdf or str(PROJECT_ROOT / "data" / pref / f"akiyouryou_{pref}.pdf")
    grid_dir = get_grid_dir(pref)
    grid_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"{pref_name} 空容量マッピングPDF → CSV抽出")
    print(f"{'='*60}")

    trans_rows, sub_rows, dist_rows = extract_pdf(pdf_path, pref_name)

    for label, rows, cols, fname in [
        ("送電線", trans_rows, TRANS_COLS, "capacity_transmission_lines.csv"),
        ("変電所", sub_rows, SUB_COLS, "capacity_substations.csv"),
        ("配電用変電所", dist_rows, DIST_COLS, "capacity_distribution_substations.csv"),
    ]:
        df = pd.DataFrame(rows, columns=cols)
        # 空行除去（No列が空のもの）
        no_col = "No"
        df = df[df[no_col].astype(str).str.strip() != ""].reset_index(drop=True)
        out = grid_dir / fname
        df.to_csv(out, index=False)
        print(f"\n{label}: {len(df)} 行 -> {out.name}")
        # サンプル表示
        key_cols = [c for c in ["電圧kV", "No", df.columns[3], "空容量_当該設備MW", "空容量_上位系等考慮MW"] if c in df.columns]
        if key_cols:
            print(df[key_cols].head(5).to_string())

    print(f"\n完了!")


if __name__ == "__main__":
    main()
