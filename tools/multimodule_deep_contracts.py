"""Connected cross-module stress contracts for control flow, type flow, and task safety.

Each family crosses the provider/helper/consumer boundary, asserts an oracle
independent of the compiled VM, and has a *semantically invalid* counterpart.
Do not label a family covered until it executes; see campaign manifests.
"""

DEEP_CONTRACTS: dict[str, tuple] = {}
INVALID_DEEP_CONTRACTS: dict[str, tuple[str, str]] = {}


def add(name, features, core, exports, dependent, dependent_exports, consumer,
        expected, invalid, diagnostic="no overloads"):
    assert name not in DEEP_CONTRACTS
    DEEP_CONTRACTS[name] = (tuple(features.split()), core, tuple(exports.split()),
                            dependent, tuple(dependent_exports.split()), consumer,
                            tuple(expected))
    INVALID_DEEP_CONTRACTS[name] = (invalid, diagnostic)

ITEM = 'public object Item => public $value:Int end\n'
BOX = 'public object[T] Box => public $value:T end\n'
CHOICE = ('public variant Choice =>\n Present => public $value:Int end\n'
          ' Empty => end\nend\n')
CODE = 'public variant Code => GOOD = @N@ end\n'
SIZED = ('public trait Sized => extend size -> Int end\n'
         'public object Item => public $value:Int end\n'
         'object Item as Sized => define size => $self.value end\n')
NAMED = ('public trait Named => public define label -> String => "default" end end\n'
         'public object Item => public $value:Int end\nobject Item as Named => end\n')
PARENT = ('public trait Parent =>\n extend size -> Int\n'
          ' public define doubled -> Int => ($self size)*2 end\nend\n'
          'public trait Child as Parent => end\n'
          'public object Item => public $value:Int end\n'
          'object Item as Child => define size => $self.value end\n')
RESOURCE = ('@nodup("Resource cannot be copied")\n'
            'public object Resource =>\n public $value:Int\n'
            ' public define read -> Int => $self.value end\nend\n')

# 1–8: Error handling must preserve nominal identity, generic bounds and
# surrounding stack/collection state through imported helper calls.
add('try_trait_dispatch', 'exception trait dispatch object method field', SIZED, 'Sized Item',
    'import {core.[Sized,Item]}\npublic define safe(x:Item)->Int =>\n'
    ' try => $x as[Sized] | size\n handle => -2 end ?!\nend\n', 'safe',
    'safe(Item(@N@))\n', ['@N@'], 'safe("bad")\n')
add('try_generic_trait_bound', 'exception generic generic_bounds trait dispatch object', SIZED, 'Sized Item',
    'import {core.Sized}\npublic define[T:Sized] safe(x:T)->Int =>\n'
    ' try => $x size\n handle => -2 end ?!\nend\n', 'safe',
    'safe(Item(@N@))\n', ['@N@'], 'safe("bad")\n')
add('try_overload_object', 'exception overload object element dispatch field', ITEM, 'Item',
    'import {core.Item}\npublic define choose(x:Item)->Int=>$x.value end\n'
    'public define choose(x:Int)->Int=>$x+1 end\n'
    'public define safe(x:Item)->Int =>\n try => choose($x)\n handle => -1 end ?!\nend\n',
    'choose safe', 'safe(Item(@N@))\nchoose(@N@)\n', ['@N@','@N_PLUS_ONE@'],
    'safe("bad")\n')
add('try_record_object', 'exception record object field construction element', ITEM, 'Item',
    'import {core.Item}\npublic define safe(x:Item)->Int =>\n'
    ' try =>\n  $r=record{item=>$x}\n  $r.item.value\n'
    ' handle => -1 end ?!\nend\n', 'safe', 'safe(Item(@N@))\n', ['@N@'], 'safe("bad")\n')
add('try_dictionary_object', 'exception dictionary object field construction element', ITEM, 'Item',
    'import {core.Item}\npublic define safe(x:Item)->Int =>\n'
    ' try =>\n  $m=dict{"item"=>$x}\n  $v=$m["item"]\n  $v.value\n'
    ' handle => -1 end ?!\nend\n', 'safe', 'safe(Item(@N@))\n', ['@N@'], 'safe("bad")\n')
add('try_variant_match', 'exception variant match construction element', CHOICE, 'Choice',
    'import {core.Choice}\npublic define safe(x:Choice)->Int =>\n'
    ' try =>\n $x match =>\n as :Present(v) => $v\n as :Empty => -1\n end\n'
    ' handle => -2 end ?!\nend\n', 'safe', 'safe(Choice.Present(@N@))\n', ['@N@'],
    'safe("bad")\n')
add('try_enum_branch', 'exception enum branch nominal element', CODE, 'Code',
    'import {core.Code}\npublic define safe(x:Code)->Int =>\n'
    ' try => $x.value\n'
    ' handle => -2 end ?!\nend\n', 'safe', 'if (true) => safe(Code.\\GOOD) else => -1 end\n', ['@N@'],
    'Code.\\MISSING\n', 'unknown element')
add('try_optional_variant_match', 'exception option variant match generic construction', CHOICE, 'Choice',
    'import {core.Choice}\npublic define safe(x:Choice?)->Int =>\n'
    ' try => $x match =>\n'
    ' as :Some(c) =>\n  $c match =>\n   as :Present(v) => $v\n   as :Empty => -1\n  end\n'
    ' as :None => -2\n end\n handle => -3 end ?!\nend\n', 'safe',
    'safe(Some(Choice.Present(@N@)))\nsafe(\\None)\n', ['@N@','-2'],
    'safe("bad")\n')

# 9–16: Branches and loops carry types across imported nominal/trait boundaries.
add('branch_trait_default', 'branch trait trait_default dispatch object method strings', NAMED, 'Named Item',
    'import {core.[Named,Item]}\npublic define display(x:Item)->String=>\n'
    ' if ($x.value > 0) => $x as[Named] | label else => "other" end\nend\n', 'display',
    'display(Item(@N@))\n', ['default'], 'display("bad")\n')
add('branch_generic_box', 'branch generic object field construction element', BOX, 'Box',
    'import {core.Box}\npublic define choose(x:Box[Int])->Int =>\n'
    ' if ($x.value > 0) => $x.value else => -1 end\nend\n', 'choose',
    'choose(Box(@N@))\n', ['@N@'], 'choose("bad")\n')
add('branch_enum_overload', 'branch enum overload nominal dispatch element', CODE, 'Code',
    'import {core.Code}\npublic define select(x:Code)->Int=>$x.value end\n'
    'public define select(x:String)->Int=>0 end\n'
    'public define choose(x:Code)->Int =>\n'
    ' if ($x.value > 0) => select($x) else => -1 end\nend\n', 'select choose',
    'choose(Code.\\GOOD)\n', ['@N@'], 'choose(true)\n')
add('branch_variant_match', 'branch variant match construction element', CHOICE, 'Choice',
    'import {core.Choice}\npublic define choose(x:Choice)->Int =>\n'
    ' if (true) =>\n  $x match =>\n   as :Present(v) => $v\n   as :Empty => -1\n  end\n'
    ' else => -2 end\nend\n', 'choose', 'choose(Choice.Present(@N@))\n', ['@N@'],
    'choose("bad")\n')
add('loop_trait_dispatch', 'loop trait object method dispatch field ownership', SIZED, 'Sized Item',
    'import {core.[Sized,Item]}\npublic define twice(x:Item)->Int =>\n'
    ' $n:Int=0\n $total:Int=0\n'
    ' while ($n < 2) =>\n  $total := + ($x as[Sized] | size)\n'
    '  $n := + 1\n end\n $total\nend\n', 'twice',
    'twice(Item(@N@))\n', ['@N_DOUBLE@'], 'twice("bad")\n')
add('loop_enum_value', 'loop enum nominal branch element numbers', CODE, 'Code',
    'import {core.Code}\npublic define twice(x:Code)->Int =>\n'
    ' $n:Int=0\n $sum:Int=0\n while ($n < 2) =>\n'
    '  $sum := + $x.value\n  $n := + 1\n end\n $sum\nend\n', 'twice',
    'twice(Code.\\GOOD)\n', ['@N_DOUBLE@'], 'Code.\\MISSING\n', 'unknown element')
add('loop_generic_box', 'loop generic object field branch element', BOX, 'Box',
    'import {core.Box}\npublic define twice(x:Box[Int])->Int =>\n'
    ' $n:Int=0\n $sum:Int=0\n while ($n < 2) =>\n'
    '  $sum := + $x.value\n  $n := + 1\n end\n $sum\nend\n', 'twice',
    'twice(Box(@N@))\n', ['@N_DOUBLE@'], 'twice("bad")\n')
add('loop_variant_match', 'loop variant match element construction', CHOICE, 'Choice',
    'import {core.Choice}\npublic define sum(x:Choice)->Int =>\n'
    ' $n:Int=0\n $total:Int=0\n while ($n < 2) =>\n'
    '  $total := + ($x match =>\n   as :Present(v) => $v\n   as :Empty => 0\n  end)\n'
    '  $n := + 1\n end\n $total\nend\n', 'sum',
    'sum(Choice.Present(@N@))\n', ['@N_DOUBLE@'], 'sum("bad")\n')

# 17–23: Higher-order function values preserve imported nominal distinctions.
add('higher_order_variant_match', 'higher_order variant match construction element', CHOICE, 'Choice',
    'import {core.Choice}\npublic define apply(f:Function[Choice -> Int],x:Choice)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Choice)->Int=>\n $x match =>\n  as :Present(v)=>$v\n  as :Empty=>-1\n end\nend,Choice.Present(@N@))\n',
    ['@N@'], 'apply(fn(x:Int)->Int=>$x end,Choice.Present(@N@))\n')
add('higher_order_enum_branch', 'higher_order enum branch nominal element', CODE, 'Code',
    'import {core.Code}\npublic define apply(f:Function[Code -> Int],x:Code)->Int=>$f($x) end\n',
    'apply', 'if (true) => apply(fn(x:Code)->Int=>$x.value end,Code.\\GOOD) else => -1 end\n',
    ['@N@'], 'apply(fn(x:String)->Int=>1 end,Code.\\GOOD)\n')
add('higher_order_trait_default', 'higher_order trait trait_default object method dispatch strings', NAMED, 'Named Item',
    'import {core.Item}\npublic define apply(f:Function[Item -> String],x:Item)->String=>$f($x) end\n',
    'apply', 'apply(fn(x:Item)->String=>$x as[Named] | label end,Item(@N@))\n',
    ['default'], 'apply(fn(x:Int)->Int=>$x end,Item(@N@))\n')
add('higher_order_generic_box', 'higher_order generic generic_specialisation object field construction', BOX, 'Box',
    'import {core.Box}\npublic define[T] apply(f:Function[T -> Int],x:T)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Box[Int])->Int=>$x.value end,Box(@N@))\n', ['@N@'],
    'apply(fn(x:String)->Int=>1 end,Box(@N@))\n')
add('higher_order_trait_dispatch', 'higher_order trait object method dispatch field generic', SIZED, 'Sized Item',
    'import {core.Item}\npublic define apply(f:Function[Item -> Int],x:Item)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Item)->Int=>$x as[Sized] | size end,Item(@N@))\n',
    ['@N@'], 'apply(fn(x:String)->Int=>1 end,Item(@N@))\n')
add('higher_order_object_field', 'higher_order object field construction', ITEM, 'Item',
    'import {core.Item}\npublic define apply(f:Function[Item -> Int],x:Item)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Item)->Int=>$x.value end,Item(@N@))\n', ['@N@'],
    'apply(fn(x:Bool)->Int=>1 end,Item(@N@))\n')
add('higher_order_inherited_trait', 'higher_order trait trait_inheritance trait_default dispatch method', PARENT, 'Parent Child Item',
    'import {core.Item}\npublic define apply(f:Function[Item -> Int],x:Item)->Int=>$f($x) end\n',
    'apply', 'apply(fn(x:Item)->Int=>$x as[Parent] | doubled end,Item(@N@))\n',
    ['@N_DOUBLE@'], 'apply(fn(x:String)->Int=>1 end,Item(@N@))\n')

# 24–30: Stacking wrappers changes generic/option/variant/trait dispatch paths.
add('generic_variant_dictionary', 'generic dictionary variant match construction type_inference', CHOICE, 'Choice',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$table=identity(dict{"x"=>Choice.Present(@N@)})\n'
    '$table["x"] match =>\n as :Present(v)=>$v\n as :Empty=>-1\nend\n', ['@N@'],
    'Choice.\\MISSING\n', 'unknown element')
add('generic_variant_record', 'generic record variant match field construction', CHOICE, 'Choice',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$row=identity(record{x=>Choice.Present(@N@)})\n'
    '$row.x match =>\n as :Present(v)=>$v\n as :Empty=>-1\nend\n', ['@N@'],
    'Choice.\\MISSING\n', 'unknown element')
add('generic_variant_tuple', 'generic tuple variant match construction type_inference', CHOICE, 'Choice',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$pair=identity({Choice.Present(@N@),Choice.Present(0)})\n'
    '$pair[0] match =>\n as :Present(v)=>$v\n as :Empty=>-1\nend\n', ['@N@'],
    'Choice.\\MISSING\n', 'unknown element')
add('generic_enum_option', 'generic enum option match nominal element', CODE, 'Code',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$x:Code?=identity(Some(Code.\\GOOD))\n'
    '$x match =>\n as :Some(code)=>$code.value\n as :None=>-1\nend\n', ['@N@'],
    'Code.\\MISSING\n', 'unknown element')
add('generic_box_record', 'generic object record construction field type_inference', BOX, 'Box',
    'public define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$row=identity(record{x=>Box(@N@)})\n'
    '$box=$row.x\n$box.value\n', ['@N@'],
    '$bad:Box[Int]=Box("bad")\n', 'cannot assign')
add('trait_default_in_option', 'trait trait_default option match object method dispatch', NAMED, 'Named Item',
    'import {core.Item}\npublic define display(x:Item?)->String =>\n'
    ' $x match =>\n  as :Some(item)=>$item as[Named] | label\n  as :None=>"none"\n end\nend\n',
    'display', 'display(Some(Item(@N@)))\ndisplay(\\None)\n', ['default','none'],
    'display("bad")\n')
add('trait_default_in_dictionary', 'trait trait_default dictionary object method dispatch', NAMED, 'Named Item',
    'import {core.[Named,Item]}\npublic define display(x:Item)->String =>\n'
    ' $table=dict{"x"=>$x}\n $v=$table["x"]\n $v as[Named] | label\nend\n',
    'display', 'display(Item(@N@))\n', ['default'], 'display("bad")\n')

# 31–36: Safe resource use across generic/exception/task/trait boundaries.
add('borrowed_generic_forwarding', 'borrowing ownership generic object method field', RESOURCE, 'Resource',
    'import {core.Resource}\npublic define[T] id(x:T)->T=>$x end\n'
    'public define inspect(x:Resource)->Int=>$x read | id end\n', 'inspect',
    '$r=Resource(@N@)\ninspect($r)\ninspect($r)\n', ['@N@','@N@'],
    'inspect("bad")\n')
add('borrowed_try_method', 'borrowing ownership exception object method field', RESOURCE, 'Resource',
    'import {core.Resource}\npublic define inspect(x:Resource)->Int =>\n'
    ' try => $x read\n handle => -1 end ?!\nend\n', 'inspect',
    '$r=Resource(@N@)\ninspect($r)\ninspect($r)\n', ['@N@','@N@'],
    'inspect("bad")\n')
add('borrowed_branch_method', 'borrowing ownership branch object method field', RESOURCE, 'Resource',
    'import {core.Resource}\npublic define inspect(x:Resource)->Int =>\n'
    ' if (true) => $x read else => -1 end\nend\n', 'inspect',
    '$r=Resource(@N@)\ninspect($r)\ninspect($r)\n', ['@N@','@N@'],
    'inspect("bad")\n')
add('borrowed_generic_trait', 'borrowing ownership generic_bounds trait dispatch object method',
    SIZED.replace('Item', 'Resource').replace('public object Resource', '@nodup("Resource cannot be copied")\npublic object Resource'), 'Sized Resource',
    'import {core.Sized}\npublic define[T:Sized] inspect(x:T)->Int=>$x size end\n', 'inspect',
    '$r=Resource(@N@)\ninspect($r)\ninspect($r)\n', ['@N@','@N@'],
    'inspect("bad")\n')
add('task_trait_dispatch', 'task concurrency trait object dispatch method', SIZED, 'Sized Item',
    'import {core.[Sized,Item]}\npublic define inspect(x:Item)->Int =>\n'
    ' $x as[Sized] | size\nend\n', 'inspect',
    '$t=fn -> Int => inspect(Item(@N@)) end | spawn\n$t wait\n', ['@N@'],
    'inspect("bad")\n')
add('task_generic_variant', 'task concurrency generic variant match construction', CHOICE, 'Choice',
    'import {core.Choice}\npublic define[T] identity(x:T)->T=>$x end\n', 'identity',
    '$t=fn -> Int =>\n identity(Choice.Present(@N@)) match =>\n'
    ' as :Present(v)=>$v\n as :Empty=>-1\n end\nend | spawn\n$t wait\n', ['@N@'],
    'Choice.\\MISSING\n', 'unknown element')
