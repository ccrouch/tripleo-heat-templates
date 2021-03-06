import sys
import yaml
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('templates', nargs='+')
parser.add_argument('--master-role', nargs='?',
                    help='Translate slave_roles to this')
parser.add_argument('--slave-roles', nargs='*',
                    help='Translate all of these to master_role')

args = parser.parse_args()

templates = args.templates

def _translate_role(role):
    global args
    if not args.master_role:
        return role
    if role == args.master_role:
        return role
    if role not in args.slave_roles:
        return role
    return args.master_role

def translate_role(role):
    r = _translate_role(role)
    if not isinstance(r, basestring):
        raise Exception('%s -> %r' % (role, r))
    return r

def resolve_params(item, param, value):
    if item == {'Ref': param}:
        return value
    if isinstance(item, dict):
        copy_item = dict(item)
        for k, v in iter(copy_item.items()):
            item[k] = resolve_params(v, param, value) 
    elif isinstance(item, list):
        copy_item = list(item)
        new_item = []
        for v in copy_item:
            new_item.append(resolve_params(v, param, value))
        item = new_item
    return item


errors = []
end_template={'HeatTemplateFormatVersion': '2012-12-12',
              'Description': []}
resource_changes=[]
for template_path in templates:
    template = yaml.safe_load(open(template_path))
    end_template['Description'].append(template.get('Description',
                                                    template_path))
    new_parameters = template.get('Parameters', {})
    for p, pbody in sorted(new_parameters.items()):
        if p in end_template.get('Parameters', {}):
            if pbody != end_template['Parameters'][p]:
                errors.append('Parameter %s from %s conflicts.' % (p,
                                                                   template_path))
            continue
        if 'Parameters' not in end_template:
            end_template['Parameters'] = {}
        end_template['Parameters'][p] = pbody

    new_outputs = template.get('Outputs', {})
    for o, obody in sorted(new_outputs.items()):
        if o in end_template.get('Outputs', {}):
            if pbody != end_template['Outputs'][p]:
                errors.append('Output %s from %s conflicts.' % (o,
                                                                   template_path))
            continue
        if 'Outputs' not in end_template:
            end_template['Outputs'] = {}
        end_template['Outputs'][o] = obody

    new_resources = template.get('Resources', {})
    for r, rbody in sorted(new_resources.items()):
        if rbody['Type'] == 'AWS::EC2::Instance':
            # XXX Assuming ImageId is always a Ref
            del end_template['Parameters'][rbody['Properties']['ImageId']['Ref']]
            role = rbody.get('Metadata', {}).get('OpenStack::Role', r)
            role = translate_role(role)
            if role != r:
                resource_changes.append((r, role))
            if role in end_template.get('Resources', {}):
                new_metadata = rbody.get('Metadata', {})
                for m, mbody in iter(new_metadata.items()):
                    if m in end_template['Resources'][role].get('Metadata', {}):
                        if m == 'OpenStack::ImageBuilder::Elements':
                            end_template['Resources'][role]['Metadata'][m].extend(mbody)
                            continue
                        if mbody != end_template['Resources'][role]['Metadata'][m]:
                            errors.append('Role %s metadata key %s conflicts.' %
                                          (role, m))
                        continue
                    end_template['Resources'][role]['Metadata'][m] = mbody
                continue
            if 'Resources' not in end_template:
                end_template['Resources'] = {}
            end_template['Resources'][role] = rbody
            ikey = '%sImage' % (role)
            end_template['Resources'][role]['Properties']['ImageId'] = {'Ref': ikey}
            end_template['Parameters'][ikey] = {'Type': 'String'}
        elif rbody['Type'] == 'FileInclude':
            with open(rbody['Path']) as rfile:
                include_content = yaml.safe_load(rfile.read())
                subkeys = rbody.get('SubKey','').split('.')
                while len(subkeys) and subkeys[0]:
                    include_content = include_content[subkeys.pop(0)]
                for replace_param, replace_value in iter(rbody.get('Parameters',
                                                                   {}).items()):
                    include_content = resolve_params(include_content,
                                                     replace_param,
                                                     replace_value)
                end_template['Resources'][r] = include_content
        else:
            if r in end_template.get('Resources', {}):
                if rbody != end_template['Resources'][r]:
                    errors.append('Resource %s from %s conflicts' % (r,
                                                                     template_path))
                continue
            if 'Resources' not in end_template:
                end_template['Resources'] = {}
            end_template['Resources'][r] = rbody

def fix_ref(item, old, new):
    if isinstance(item, dict):
        copy_item = dict(item)
        for k, v in sorted(copy_item.items()):
            if k == 'Ref' and v == old:
                item[k] = new
                continue
            if k == 'DependsOn' and v == old:
                item[k] = new
                continue
            if k == 'Fn::GetAtt' and isinstance(v, list) and v[0] == old:
                new_list = list(v)
                new_list[0] = new
                item[k] = new_list
                continue
            if k == 'AllowedResources' and isinstance(v, list) and old in v:
                while old in v:
                    pos = v.index(old)
                    v[pos] = new
                continue
            fix_ref(v, old, new)
    elif isinstance(item, list):
        copy_item = list(item)
        for v in item:
            fix_ref(v, old, new)

for change in resource_changes:
    fix_ref(end_template, change[0], change[1])
            
if errors:
    for e in errors:
        sys.stderr.write("ERROR: %s\n" % e)
end_template['Description'] = ','.join(end_template['Description'])
sys.stdout.write(yaml.safe_dump(end_template, default_flow_style=False))
