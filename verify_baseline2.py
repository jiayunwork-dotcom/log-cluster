"""正确验证基线比对功能"""
import json

with open('./output2/report.json') as f:
    d = json.load(f)

anom = d.get('anomalies', {})
new_ids = anom.get('new_templates', [])
err_ids = anom.get('error_templates', [])

# 通过template_id反查
templates_by_id = {t['template_id']: t for t in d['templates']}
new_t = [templates_by_id[i]['template_str'] for i in new_ids]
err_t = [templates_by_id[i]['template_str'] for i in err_ids]

print('=========== 无基线对照 ===========')
print(f'新模板数量: {len(new_t)}')
print(f'错误模板数量: {len(err_t)}')
for t in err_t:
    print(f'  ERR: {t}')

print()

with open('./output/report.json') as f:
    d = json.load(f)

anom = d.get('anomalies', {})
new_ids = anom.get('new_templates', [])
err_ids = anom.get('error_templates', [])
templates_by_id = {t['template_id']: t for t in d['templates']}
new_t = [templates_by_id[i]['template_str'] for i in new_ids]
err_t = [templates_by_id[i]['template_str'] for i in err_ids]

print('=========== 有基线（正确对照） ===========')
print(f'新模板数量: {len(new_t)} (应该为0)')
print(f'错误模板数量: {len(err_t)} (应与无基线相同)')
for t in err_t:
    print(f'  ERR: {t}')

print()
print('===== 测试结果 =====')
all_pass = True
if len(new_t) != 0:
    print('❌ 基线比对失败: 仍有新模板被标记')
    for t in new_t:
        print(f'  新模板: {t}')
    all_pass = False
if len(err_t) == 0:
    print('⚠️  警告: 无错误模板，但示例日志应该有ERROR级别日志')
else:
    print(f'✅ 错误模板识别正常: {len(err_t)}个')
if all_pass and len(new_t) == 0:
    print('✅✅✅ 基线比对功能完全修复!')
