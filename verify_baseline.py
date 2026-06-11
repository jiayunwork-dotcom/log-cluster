"""验证基线比对功能"""
import json
with open('./output/report.json') as f:
    d = json.load(f)
new_t = [t['template_str'] for t in d.get('anomaly_report', {}).get('new_templates', [])]
err_t = [t['template_str'] for t in d.get('anomaly_report', {}).get('error_templates', [])]
print(f'新模板数量: {len(new_t)}')
print(f'错误模板数量: {len(err_t)}')
print('===== 基线比对结果: =====')
if len(new_t) == 0:
    print('✅ PASS: 所有模板都被正确识别为已有模板（无\"新模板\"告警）')
else:
    print('❌ FAIL (仍有新模板):')
    for t in new_t:
        print(f'  - {t}')
print()
if err_t:
    print(f'以下错误模板正常保留（共{len(err_t)}个，这是正确的）：')
    for t in err_t:
        print(f'  * {t[:70]}...' if len(t) > 70 else f'  * {t}')
