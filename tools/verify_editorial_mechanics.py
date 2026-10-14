"""Verify gameplay AST against the V1.4.3 base after reversing only editorial text calls."""
from pathlib import Path
import ast,json,subprocess
from string import Formatter
root=Path(__file__).resolve().parents[1]
import os
os.chdir(root)
BASE="5ab332e28ebef415a3b0757787109a8d60acd2a8"
defaults={}
for p in (root/'app/mini/editorial/defaults/ru').glob('*.json'):
    defaults.update(json.loads(p.read_text(encoding='utf-8')))
class RestoreEditorial(ast.NodeTransformer):
    def visit_Call(self,node):
        node=self.generic_visit(node)
        if isinstance(node.func,ast.Name) and node.func.id=='editorial_text' and node.args and isinstance(node.args[0],ast.Constant):
            value=defaults[node.args[0].value]
            if not isinstance(value,str):return node
            args={k.arg:k.value for k in node.keywords}
            values=[]
            for literal,name,_,_ in Formatter().parse(value):
                if literal:values.append(ast.Constant(literal))
                if name:
                    arg=args[name]
                    if isinstance(arg,ast.JoinedStr):values.extend(arg.values)
                    else:values.append(ast.FormattedValue(value=arg,conversion=-1,format_spec=None))
            if not args:return ast.Constant(value=value.replace('{{','{').replace('}}','}'))
            return ast.JoinedStr(values=values)
        return node
    def visit_ImportFrom(self,node):
        if node.module and node.module.startswith('app.mini.editorial'):return None
        return node

def normalize(tree):
    for node in ast.walk(tree):
        if isinstance(node,ast.JoinedStr):
            merged=[]
            for value in node.values:
                if isinstance(value,ast.Constant) and merged and isinstance(merged[-1],ast.Constant):
                    merged[-1].value+=value.value
                else:merged.append(value)
            node.values=merged
    return ast.dump(tree,include_attributes=False)
changed=subprocess.check_output(['git','diff',BASE,'--name-only'],stderr=subprocess.DEVNULL).decode().splitlines()
checked=[]
for rel in changed:
    if not rel.startswith('app/mini/') or not rel.endswith('.py'):continue
    p=root/rel
    if not (p.name in ('service.py','combat.py','wallet.py','heroes.py','gacha.py','items.py','players.py','hero_upgrades.py','favorites.py','availability.py','daily.py','worlds.py','admin_grants.py','builtins.py','shards.py','shop.py','registration.py','repository.py','rewards.py','runtime.py','access.py','state.py') and '/ui/' not in rel):continue
    original=ast.parse(subprocess.check_output(['git','show',BASE+':'+rel]).decode('utf-8'))
    restored=RestoreEditorial().visit(ast.parse(p.read_text(encoding='utf-8-sig')))
    assert normalize(original)==normalize(restored), f'Non-presentation AST differs: {rel}'
    checked.append(rel)
print('OK: gameplay AST unchanged after restoring editorial strings:',len(checked))
print('\n'.join(checked))
