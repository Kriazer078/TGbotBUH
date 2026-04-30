file_path = 'c:/Users/User.DESKTOP-T27SALG/Downloads/project/tgbotbuh/bot/services/ai_service.py'

with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()

replacement = [
    'RATES_2026 = {\n',
    '    "МЗП":       85_000,\n',
    '    "МРП":        4_200,\n',
    '    "ОПВ":         0.10,\n',
    '    "ВОСМС":       0.02,\n',
    '    "ИПН":         0.10,\n',
    '    "СО":          0.035,\n',
    '    "ОСМС":        0.03,\n',
    '    "ОПВр":        0.015,\n',
    '    "СН":          0.095,\n',
    '    "НДС":         0.12,\n',
    '    "КПН":         0.20,\n',
    '}\n',
    '\n',
    '# ── Системная инструкция ───────────────────────────────────────────────────────\n'
]

# We want to replace lines 27-31 (indices 26-30)
# Let's verify line 26 is RATES_2026
if lines[26].startswith('RATES_2026'):
    lines[26:31] = replacement
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("Fixed successfully.")
else:
    print(f"Error: Line 27 is '{lines[26]}', expected RATES_2026")
