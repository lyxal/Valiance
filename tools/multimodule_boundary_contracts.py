"""Connected contracts for runtime boundaries under nested imported types.

Never count a declared fixture as verified without executing the designated
source, compiled-artifact and negative diagnostic campaigns.
"""

BOUNDARY_CONTRACTS = {}
INVALID_BOUNDARY_CONTRACTS = {}

def add(name, axes, provider, exports, helper, helpers, consumer, expected, invalid, diagnostic='no overloads'):
    assert name not in BOUNDARY_CONTRACTS
    BOUNDARY_CONTRACTS[name] = (tuple(axes.split()), provider, tuple(exports.split()),
                                helper, tuple(helpers.split()), consumer, tuple(expected))
    INVALID_BOUNDARY_CONTRACTS[name] = (invalid, diagnostic)

ITEM = 'public object Item=>public $value:Int end\n'
BOX = 'public object[T] Box=>public $value:T end\n'
VARIANT = ('public variant Choice=>\n'
           ' Present=>public $value:Int end\n Empty=>end\nend\n')
CODE = 'public variant Code=>GOOD=@N@ end\n'
SIZED = ('public trait Sized=>extend size->Int end\n'
         'public object Item=>public $value:Int end\n'
         'object Item as Sized=>define size=>$self.value end\n')
NAMED = ('public trait Named=>public define label->String=>"default" end end\n'
         'public object Item=>public $value:Int end\n'
         'object Item as Named=>end\n')
VALID_TAG = ('public tag #valid as computed\n'
             'public define #valid(v:Int)->#boolean Number=>$v>0 end\n')
STICKY_TAG = ('public tag #sticky as constructed\n'
              'public define increment(v:Number)->Number=>$v+1 end\n'
              '#sticky: increment =>\n (#sticky Number)->#sticky Number\nend\n')
MARKED_TAG = 'public tag #marked as computed\n'
UNIT_TAG = 'public tag #metres as unit\n'

# Reflection is executed against nominal layouts originating in another module.
add('reflection_generic_optional', 'reflection generic generic_specialisation object option match field', BOX, 'Box',
    'import {core.Box}\npublic define read(x:Box[Int]?)->Int =>\n'
    ' $x match =>\n  as :Some(box)=>$box[fields[Box[Int]]["value"]]\n  as :None=>-1\n end\nend\n', 'read',
    'read(Some(Box(@N@)))\n',
    ['@N@'], 'read("bad")\n')
add('reflection_generic_record', 'reflection generic object record field construction', BOX, 'Box',
    'import {core.Box}\npublic define read(x:Box[Int])->Int=>$x[fields[Box[Int]]["value"]] end\n', 'read',
    '$row=record{item=>Box(@N@)}\nread($row.item)\n', ['@N@'], 'read("bad")\n')
add('reflection_generic_dictionary', 'reflection generic object dictionary field construction', BOX, 'Box',
    'import {core.Box}\npublic define read(x:Box[Int])->Int=>$x[fields[Box[Int]]["value"]] end\n', 'read',
    '$map=dict{"x"=>Box(@N@)}\nread($map["x"])\n', ['@N@'], 'read("bad")\n')
add('reflection_trait_dispatch', 'reflection trait object dispatch method field', SIZED, 'Sized Item',
    'import {core.[Sized,Item]}\npublic define read(x:Item)->Int =>\n'
    ' $f=fields[Item]["value"]\n ($x[$f]) + ($x as[Sized] | size) - $x.value\nend\n', 'read',
    'read(Item(@N@))\n', ['@N@'], 'read("bad")\n')
add('reflection_higher_order', 'reflection higher_order object field element', ITEM, 'Item',
    'import {core.Item}\npublic define apply(f:Function[Item -> Int],x:Item)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Item)->Int=>$x[fields[Item]["value"]] end,Item(@N@))\n',
    ['@N@'], 'apply(fn(x:String)->Int=>1 end,Item(@N@))\n')
add('reflection_list_map', 'reflection finite_list higher_order object field element', ITEM, 'Item',
    'import {core.Item}\npublic define read(xs:Item+ novec)->Int =>\n'
    ' $ys=$xs map: fn(x:Item)->Int=>$x[fields[Item]["value"]] end\n $ys[0]\nend\n',
    'read', 'read([Item(@N@),Item(0)])\n', ['@N@'], 'read("bad")\n')
add('reflection_try_generic', 'reflection exception generic object field casts', BOX, 'Box',
    'import {core.Box}\npublic define read(x:Box[Int])->Int =>\n'
    ' try => $x[fields[Box[Int]]["value"]]\n handle => -1 end ?!\nend\n', 'read',
    'read(Box(@N@))\n', ['@N@'], 'read("bad")\n')

# JSON is called at the consumer boundary, so the test does NOT mask the
# pre-existing failure linking std.json inside a transitive imported helper.
add('json_generic_box', 'json generic object field strings construction', BOX, 'Box',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    'import {std.json}\njson.encode(identity(Box(@N@))) ?!\n', ['{"value":@N@}'],
    '$bad:Box[Int]=Box("bad")\n', 'cannot assign')
add('json_object_record', 'json record object field construction strings', ITEM, 'Item',
    'import {core.Item}\npublic define relay(x:Item)->Item=>$x end\n', 'relay',
    'import {std.json}\njson.encode(record{item=>relay(Item(@N@))}) ?!\n',
    ['{"item":{"value":@N@}}'], 'relay("bad")\n')
add('json_object_dictionary', 'json dictionary object field construction strings', ITEM, 'Item',
    'import {core.Item}\npublic define relay(x:Item)->Item=>$x end\n', 'relay',
    'import {std.json}\njson.encode(dict{"item"=>relay(Item(@N@))}) ?!\n',
    ['{"item":{"value":@N@}}'], 'relay("bad")\n')
add('json_generic_list', 'json generic finite_list object construction strings', BOX, 'Box',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    'import {std.json}\njson.encode(identity([Box(@N@),Box(0)])) ?!\n',
    ['[{"value":@N@},{"value":0}]'], '$bad:Box[Int]=Box("bad")\n', 'cannot assign')
add('json_enum_object', 'json enum nominal object field strings element', CODE, 'Code',
    'public object Holder=>public $value:Int end\n'
    'public define[T] identity(x:T)->T=>$x end\n', 'Holder identity',
    'import {std.json}\njson.encode(identity(Holder(Code.\\GOOD.value))) ?!\n',
    ['{"value":@N@}'], 'Code.\\MISSING\n', 'unknown element')
add('json_trait_default_record', 'json trait trait_default object dispatch record strings', NAMED, 'Named Item',
    'import {core.[Named,Item]}\npublic define renderName(x:Item)->String=>$x as[Named] | label end\n',
    'renderName', 'import {std.json}\njson.encode(record{name=>renderName(Item(@N@))}) ?!\n',
    ['{"name":"default"}'], 'renderName("bad")\n')

# Tags crossing other type/effect layers should retain validation or overlay
# semantics, and invalid tags must produce diagnostic errors (not parse faults).
add('tag_validator_generic_box', 'tag tag_validator computed generic object field construction', VALID_TAG, '#valid',
    'public object[T] Box=>public $value:T end\n', 'Box',
    '$b=Box(@N@ #valid)\n$b.value | #-valid\n', ['@N@'],
    '@N@ #unknown\n', 'unknown data tag')
add('tag_validator_variant_match', 'tag tag_validator computed variant match construction', VALID_TAG, '#valid',
    'public variant Choice=>\n Present=>public $value:Int end\n Empty=>end\nend\n',
    'Choice', 'Choice.Present(@N@ #valid | #-valid) match =>\n'
    ' as :Present(v)=>$v\n as :Empty=>-1\nend\n', ['@N@'],
    '@N@ #unknown\n', 'unknown data tag')
add('tag_validator_dictionary', 'tag tag_validator computed dictionary generic type_inference', VALID_TAG, '#valid',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$map=identity(dict{"x"=>@N@ #valid | #-valid})\n$map["x"]\n', ['@N@'],
    '@N@ #unknown\n', 'unknown data tag')
add('tag_validator_record', 'tag tag_validator computed record generic field', VALID_TAG, '#valid',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$row=identity(record{x=>@N@ #valid | #-valid})\n$row.x\n', ['@N@'],
    '@N@ #unknown\n', 'unknown data tag')
add('tag_constructed_enum', 'tag constructed enum nominal element', 'public tag #sticky as constructed\n', '#sticky',
    'public variant Code=>GOOD=@N@ end\n', 'Code',
    '$value=Code.\\GOOD.value\n$value #sticky | #-sticky\n', ['@N@'],
    '@N@ #missing\n', 'unknown data tag')
add('tag_overlay_object', 'tag tag_overlay constructed object field construction', STICKY_TAG, '#sticky increment',
    'public object Item=>public $value:Int end\n', 'Item',
    '$i=Item(@N@)\n$i.value #sticky | increment | #-sticky\n', ['@N_PLUS_ONE@'],
    '@N@ #missing\n', 'unknown data tag')
add('tag_overlay_trait_method', 'tag tag_overlay constructed trait object dispatch method', STICKY_TAG, '#sticky increment',
    'import {core.increment}\npublic trait Sized=>extend size->Int end\n'
    'public object Item=>public $value:Int end\n'
    'object Item as Sized=>define size=>$self.value end\n', 'Sized Item',
    'Item(@N@) as[Sized] | size | #sticky | increment | #-sticky\n',
    ['@N_PLUS_ONE@'], '@N@ #missing\n', 'unknown data tag')
add('unit_tag_generic_option', 'tag unit generic option match enum', UNIT_TAG, '#metres',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$x:Int?=Some(identity(@N@ #metres | #-metres))\n'
    '$x match =>\n as :Some(n)=>$n\n as :None=>-1\nend\n', ['@N@'],
    '@N@ #missing\n', 'unknown data tag')
add('computed_tag_higher_order', 'tag computed higher_order object field generic', MARKED_TAG, '#marked',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$f=identity(fn(x:Int)->Int=>$x #marked | #-marked end)\n$f(@N@)\n', ['@N@'],
    '@N@ #missing\n', 'unknown data tag')

# Real scheduler-backed tasks/collections rather than merely tagging a
# source fixture as concurrent.
add('task_tag_generic', 'task concurrency tag computed generic element', MARKED_TAG, '#marked',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$t=fn -> Int=>identity(@N@ #marked | #-marked) end | spawn\n$t wait\n',
    ['@N@'], '@N@ #missing\n', 'unknown data tag')
add('task_reflection_object', 'task concurrency reflection object field element', ITEM, 'Item',
    'import {core.Item}\npublic define read(x:Item)->Int=>$x[fields[Item]["value"]] end\n', 'read',
    '$t=fn -> Int=>read(Item(@N@)) end | spawn\n$t wait\n', ['@N@'],
    'read("bad")\n')
add('task_json_generic', 'task concurrency json generic object field construction strings', BOX, 'Box',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    'import {std.json}\n$t=fn -> String =>\n json.encode(identity(Box(@N@))) ?!\nend | spawn\n$t wait\n', ['{"value":@N@}'],
    '$bad:Box[Int]=Box("bad")\n', 'cannot assign')
