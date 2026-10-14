"""Verify existing gameplay AST, permitting only the exact reviewed PvP entry guard."""
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
NEW_GAMEPLAY={"app/mini/events/pvp.py","app/mini/events/pvp_handlers.py","app/mini/events/pvp_publication.py","app/mini/migrations/v1_4_3.py"}
def remove_authorized_pvp_guard(tree,rel):
    if rel!="app/mini/events/service.py":return tree
    hook=ast.parse("from app.mini.events.pvp import assert_no_active_pvp\nassert_no_active_pvp(conn, player_id)").body
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_start")
    matches=[i for i in range(len(function.body)-1)
             if all(ast.dump(function.body[i+j])==ast.dump(hook[j]) for j in range(2))]
    assert len(matches)==1,"Events must contain exactly the reviewed bidirectional PvP guard"
    del function.body[matches[0]:matches[0]+2]
    return tree

for rel in changed:
    if not rel.startswith('app/mini/') or not rel.endswith('.py'):continue
    p=root/rel
    if rel in NEW_GAMEPLAY:
        print('NEW gameplay (dedicated transactional tests; no editorial equivalence claimed):',rel)
        continue
    if not (p.name in ('service.py','combat.py','wallet.py','heroes.py','gacha.py','items.py','players.py','hero_upgrades.py','favorites.py','availability.py','daily.py','worlds.py','admin_grants.py','builtins.py','shards.py','shop.py','registration.py','repository.py','rewards.py','runtime.py','access.py','state.py') and '/ui/' not in rel):continue
    original=ast.parse(subprocess.check_output(['git','show',BASE+':'+rel]).decode('utf-8'))
    restored=RestoreEditorial().visit(remove_authorized_pvp_guard(ast.parse(p.read_text(encoding='utf-8-sig')),rel))
    assert normalize(original)==normalize(restored), f'Non-presentation AST differs: {rel}'
    checked.append(rel)
print('OK: existing gameplay AST unchanged after restoring editorial strings and the exact authorized Events PvP guard:',len(checked))
print('\n'.join(checked))
