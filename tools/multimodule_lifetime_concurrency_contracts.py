"""Connected inter-module state, lifetime, channel and task oracles.

Every family moves a value or dynamic obligation across two owners.  The first
module supplies a nominal type, tag or trait.  The second consumes that exact
public declaration (or defines the transport type) before the script checks
an independently specified result.  Optional stdout expectations are used for
lifetime effects not represented in the returned value stack.
"""

SIZED = ('public trait Sized => extend size -> Int end\n'
         'public object Item => public $value:Int end\n'
         'object Item as Sized => define size => $self.value end\n')
NAMED = ('public trait Named => public define label -> String => "default" end end\n'
         'public object Item => public $value:Int end\n'
         'object Item as Named => end\n')
COUNTER = ('public trait Counter =>\n extend increment -> $self\n'
           ' extend count -> Int\nend\n'
           'public object Item => public $n:Int end\n'
           'object Item as Counter =>\n'
           ' @self define increment => $self.n := + 1\n'
           ' define count => $self.n\nend\n')
CHOICE = ('public variant Choice =>\n'
          ' Present => public $value:Int end\n'
          ' Empty => end\nend\n')
BOX = 'public object[T] Box => public $value:T end\n'
CODE = 'public enum[Int] Code =>\n GOOD = @N@\n BAD = -1\nend\n'

# Valid contracts: features, provider, exports, dependent, exports, caller,
# expected stack (and optionally exact destructor stdout).
LIFETIME_CONCURRENCY_CONTRACTS = {
    'mutable_trait_reconstruction': (
        ('ownership', 'trait', 'dispatch', 'method', 'object', 'field', 'construction'),
        COUNTER, ('Counter', 'Item'),
        'import {core.[Counter,Item]}\n'
        'public define advance(value:Counter)->Counter => $value increment end\n', ('advance',),
        '$old=Item(@N@)\n$new=advance($old)\n$old as[Counter] | count\n$new count\n',
        ('@N@', '@N_PLUS_ONE@')),
    'mutable_trait_through_variant': (
        ('ownership', 'trait', 'dispatch', 'method', 'variant', 'match', 'object'),
        COUNTER, ('Counter', 'Item'),
        'import {core.Item}\npublic variant Wrap =>\n Present=>public $value:Item end\n Empty=>end\nend\n', ('Wrap',),
        '$old=Item(@N@)\n'
        '$new=Wrap.Present($old) match =>\n'
        ' as :Present(item) => $item increment\n as :Empty => Item(0)\n end\n'
        '$old count\n$new count\n', ('@N@', '@N_PLUS_ONE@')),
    'mutable_generic_object_in_dictionary': (
        ('ownership', 'generic', 'object', 'dictionary', 'field', 'construction'),
        BOX, ('Box',),
        'import {core.Box}\npublic define mutate(value:Box[Int])->Int =>\n'
        ' $table=dict{"x"=>$value}\n'
        ' $table["x"].value=@N_PLUS_ONE@\n'
        ' $table["x"].value\nend\n', ('mutate',),
        'mutate(Box(@N@))\n', ('@N_PLUS_ONE@',)),
    'mutable_generic_object_in_list': (
        ('ownership', 'generic', 'object', 'finite_list', 'field', 'construction'),
        BOX, ('Box',),
        'import {core.Box}\npublic define mutate(value:Box[Int])->Int =>\n'
        ' $items=[$value]\n $items[0].value=@N_PLUS_ONE@\n'
        ' $items[0].value\nend\n', ('mutate',),
        'mutate(Box(@N@))\n', ('@N_PLUS_ONE@',)),
    'trait_duplication_through_generic': (
        ('ownership', 'trait', 'generic', 'dispatch', 'object', 'element'),
        SIZED, ('Sized', 'Item'),
        'import {core.Sized}\npublic define copySize(value:Sized)->Int, Int =>\n'
        ' $value dup | size | swap | size\nend\n', ('copySize',),
        'Item(@N@) copySize\n', ('@N@', '@N@')),
    'trait_duplication_from_union': (
        ('ownership', 'trait', 'union', 'dispatch', 'object', 'element'),
        SIZED, ('Sized', 'Item'),
        'import {core.Sized}\npublic define duplicate(value:Sized)->Int =>\n'
        ' $value dup | size | swap | size | +\nend\n', ('duplicate',),
        '$item:Item|String=Item(@N@)\nduplicate($item as![Item])\n', ('@N_DOUBLE@',)),
    'generic_trait_move': (
        ('ownership', 'generic', 'trait', 'dispatch', 'object', 'construction'),
        SIZED, ('Sized', 'Item'),
        'import {core.Sized}\npublic define[T:Sized] forward(value:T)->T=>$value end\n', ('forward',),
        'forward(Item(@N@)) as[Sized] | size\n', ('@N@',)),
    'stack_swap_imported_enum': (
        ('stack', 'enum', 'element', 'generic', 'nominal'),
        CODE, ('Code',),
        'public define[T] identity(value:T)->T=>$value end\n', ('identity',),
        'Code.\\GOOD.value | identity\n 1 | swap\n', ('1', '@N@')),
    'stack_dup_generic_box': (
        ('stack', 'ownership', 'generic', 'object', 'field'),
        BOX, ('Box',),
        'public define[T] forward(value:T)->T=>$value end\n', ('forward',),
        'Box(@N@) | forward | dup | $.value | swap | $.value\n', ('@N@', '@N@')),
    'stack_pop_tagged_generic': (
        ('stack', 'tag', 'constructed', 'generic', 'element'),
        'public tag #sticky as constructed\n', ('#sticky',),
        'public define[T] forward(value:T)->T=>$value end\n', ('forward',),
        '@N@ #sticky | forward | dup | pop | #-sticky\n', ('@N@',)),
    'spawn_generic_object_field': (
        ('task', 'concurrency', 'generic', 'object', 'field', 'construction'),
        BOX, ('Box',),
        'import {core.Box}\npublic define read(box:Box[Int])->Int=>$box.value end\n', ('read',),
        '$task=fn -> Int => read(Box(@N@)) end | spawn\n$task wait\n', ('@N@',)),
    'spawn_trait_dynamic_dispatch': (
        ('task', 'concurrency', 'trait', 'dispatch', 'object', 'method'),
        SIZED, ('Sized', 'Item'),
        'import {core.Sized}\npublic define measure(value:Sized)->Int=>$value size end\n', ('measure',),
        '$task=fn -> Int => measure(Item(@N@)) end | spawn\n$task wait\n', ('@N@',)),
    'spawn_variant_match': (
        ('task', 'concurrency', 'variant', 'match', 'construction', 'element'),
        CHOICE, ('Choice',),
        'import {core.Choice}\npublic define select(value:Choice)->Int =>\n'
        ' $value match =>\n as :Present(n)=>$n\n as :Empty=>-1\n end\nend\n', ('select',),
        '$task=fn -> Int => select(Choice.Present(@N@)) end | spawn\n$task wait\n', ('@N@',)),
    'spawn_enum_overload': (
        ('task', 'concurrency', 'enum', 'overload', 'dispatch', 'nominal'),
        CODE, ('Code',),
        'import {core.Code}\n'
        'public define select(value:Int)->Int=>$value+1 end\n'
        'public define select(value:Code)->Int=>$value.value end\n', ('select',),
        '$task=fn -> Int => select(Code.\\GOOD) end | spawn\n$task wait\n', ('@N@',)),
    'spawn_trait_default': (
        ('task', 'concurrency', 'trait', 'trait_default', 'object', 'method'),
        NAMED, ('Named', 'Item'),
        'import {core.Named}\npublic define labelOf(value:Named)->String=>$value label end\n', ('labelOf',),
        '$task=fn -> String => labelOf(Item(@N@)) end | spawn\n$task wait\n', ('default',)),
    'spawn_generic_higher_order': (
        ('task', 'concurrency', 'higher_order', 'generic', 'object', 'element'),
        BOX, ('Box',),
        'import {core.Box}\npublic define[T] apply(f:Function[T -> Int], value:T)->Int=>$f($value) end\n', ('apply',),
        '$task=fn -> Int => apply(fn(b:Box[Int])->Int=>$b.value end, Box(@N@)) end | spawn\n'
        '$task wait\n', ('@N@',)),
    'spawn_tagged_value': (
        ('task', 'concurrency', 'tag', 'computed', 'generic', 'element'),
        'public tag #marked as computed\n', ('#marked',),
        'public define[T] identity(value:T)->T=>$value end\n', ('identity',),
        '$task=fn -> Int => @N@ #marked | #-marked | identity end | spawn\n$task wait\n', ('@N@',)),
    'channel_generic_object_payload': (
        ('channel', 'concurrency', 'object', 'generic', 'construction', 'field'),
        BOX, ('Box',),
        'import {core.Box}\npublic define transport(value:Box[Int])->Receive[Box[Int]] =>\n'
        ' $c=1 Channel[Box[Int]]\n $c $value send\n $c receive\nend\n', ('transport',),
        'transport(Box(@N@))\n', ('@RECEIVE_BOX@',)),
    'channel_trait_payload': (
        ('channel', 'concurrency', 'trait', 'dispatch', 'object', 'method'),
        SIZED, ('Sized', 'Item'),
        'import {core.Sized}\npublic define transport(value:Sized)->Receive[Sized] =>\n'
        ' $c=1 Channel[Sized]\n $c $value send\n $c receive\nend\n', ('transport',),
        'transport(Item(@N@))\n', ('@RECEIVE_ITEM@',)),
    'channel_enum_payload': (
        ('channel', 'concurrency', 'enum', 'nominal', 'construction', 'element'),
        CODE, ('Code',),
        'import {core.Code}\npublic define transport(value:Code)->Receive[Code] =>\n'
        ' $c=1 Channel[Code]\n $c $value send\n $c receive\nend\n', ('transport',),
        'transport(Code.\\GOOD)\n', ('@RECEIVE_CODE@',)),
    'channel_variant_payload': (
        ('channel', 'concurrency', 'variant', 'match', 'construction', 'element'),
        CHOICE, ('Choice',),
        'import {core.Choice}\npublic define transport(value:Choice)->Receive[Choice] =>\n'
        ' $c=1 Channel[Choice]\n $c $value send\n $c receive\nend\n', ('transport',),
        'transport(Choice.Present(@N@))\n', ('@RECEIVE_CHOICE@',)),
    'channel_generic_payload': (
        ('channel', 'concurrency', 'generic', 'generic_specialisation', 'element', 'finite_list'),
        'public define[T] id(value:T)->T=>$value end\n', ('id',),
        'import {core.id}\npublic define[T] transport(value:T)->Receive[T] =>\n'
        ' $c=1 Channel[T]\n $c id($value) send\n $c receive\nend\n', ('transport',),
        'transport(@N@)\n', ('@RECEIVE_NUMBER@',)),
    'borrowed_nodup_method_repetition': (
        ('borrowing', 'ownership', 'method', 'object', 'field', 'element'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource =>\n public $value:Int\n'
        ' public define read -> Int => $self.value end\nend\n', ('Resource',),
        'import {core.Resource}\npublic define inspect(value:Resource)->Int=>$value read end\n', ('inspect',),
        '$resource=Resource(@N@)\ninspect($resource)\ninspect($resource)\n', ('@N@', '@N@')),
    'borrowed_nodup_generic_repetition': (
        ('borrowing', 'ownership', 'generic', 'generic_bounds', 'trait', 'dispatch'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource => public $value:Int end\n'
        'public trait Sized => extend size -> Int end\n'
        'object Resource as Sized => define size => $self.value end\n', ('Resource', 'Sized'),
        'import {core.Sized}\npublic define[T:Sized] inspect(value:T)->Int=>$value size end\n', ('inspect',),
        '$resource=Resource(@N@)\ninspect($resource)\ninspect($resource)\n', ('@N@', '@N@')),
    'borrowed_nodup_trait_dispatch': (
        ('borrowing', 'ownership', 'trait', 'dispatch', 'method', 'object'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource => public $value:Int end\n'
        'public trait Sized => extend size -> Int end\n'
        'object Resource as Sized => define size => $self.value end\n', ('Resource', 'Sized'),
        'import {core.Sized}\npublic define inspect(value:Sized)->Int=>$value size end\n', ('inspect',),
        '$resource=Resource(@N@)\ninspect($resource)\ninspect($resource)\n', ('@N@', '@N@')),
    'borrowed_nodup_stack_permutation': (
        ('borrowing', 'ownership', 'stack', 'method', 'object', 'field'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource =>\n public $value:Int\n'
        ' public define read -> Int=>$self.value end\nend\n', ('Resource',),
        'import {core.Resource}\npublic define inspect(value:Resource)->Int=>$value read end\n', ('inspect',),
        '$resource=Resource(@N@)\n'
        '$resource 0 | swap | swap | pop | inspect\n'
        '$resource 1 | swap | swap | pop | inspect\n', ('@N@', '@N@')),
    'borrowed_nodup_tag_view': (
        ('borrowing', 'ownership', 'tag', 'constructed', 'object', 'method'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource =>\n public $value:Int\n'
        ' public define read->Int=>$self.value end\nend\n'
        'public tag #sticky as constructed\n', ('Resource', '#sticky'),
        'import {core.[Resource,#sticky]}\npublic define inspect(value:Resource)->#sticky Int=>$value read #sticky end\n', ('inspect',),
        '$resource=Resource(@N@)\n'
        'inspect($resource) | #-sticky\n'
        'inspect($resource) | #-sticky\n', ('@N@', '@N@')),
    'borrowed_nodup_record_read': (
        ('borrowing', 'ownership', 'record', 'object', 'field', 'element'),
        '@nodup("Resource cannot be copied")\n'
        'public object Resource=>\n public $value:Int\n'
        ' public define read->Int=>$self.value end\nend\n', ('Resource',),
        'import {core.Resource}\npublic define inspect(value:Resource)->Int=>$value read end\n', ('inspect',),
        '$resource=Resource(@N@)\n'
        '$reading=inspect($resource)\n'
        'record{x=>$reading}.x\n'
        'inspect($resource)\n', ('@N@', '@N@')),
    'destructor_through_trait': (
        ('destructor', 'ownership', 'trait', 'object', 'dispatch', 'method'),
        'public trait Marker=>end\n'
        'public object Item =>\n public $value:Int\n'
        ' define ~Item => "released ${$self.value}" println\nend\n'
        'object Item as Marker=>end\n', ('Marker', 'Item'),
        'import {core.Marker}\npublic define consume(value:Marker)->Int=>\n'
        ' $value pop\n 1\nend\n', ('consume',),
        'consume(Item(@N@))\n', ('1',), 'released @N@\n'),
    'destructor_generic_forward': (
        ('destructor', 'ownership', 'generic', 'trait', 'object', 'element'),
        'public trait Marker=>end\n'
        'public object Item =>\n public $value:Int\n'
        ' define ~Item => "freed ${$self.value}" println\nend\n'
        'object Item as Marker=>end\n', ('Marker', 'Item'),
        'import {core.Marker}\npublic define[T:Marker] discard(value:T)->Int=>\n'
        ' $value pop\n 2\nend\n', ('discard',),
        'discard(Item(@N@))\n', ('2',), 'freed @N@\n'),
    'destructor_nested_owner': (
        ('destructor', 'ownership', 'object', 'field', 'construction', 'nominal'),
        'public object Child=>\n public $value:Int\n'
        ' define ~Child=>"child ${$self.value}" println\nend\n', ('Child',),
        'import {core.Child}\n'
        'public object Holder=> private $child:Child end\n'
        'public define discard(value:Holder)->Int=>$value pop | 3 end\n', ('Holder', 'discard'),
        'discard(Holder(Child(@N@)))\n', ('3',), 'child @N@\n'),
}

# Targeted regressions for imported private lifecycle hooks. They remain
# separate from the pairwise registry until every graph/layout is exercised.
IMPORTED_DESTRUCTOR_REGRESSIONS = {
    key: LIFETIME_CONCURRENCY_CONTRACTS.pop(key)
    for key in ("destructor_through_trait", "destructor_generic_forward",
                "destructor_nested_owner")
}

# Each rejection names a source-visible, imported type/member/call.  Type errors
# must remain errors through public re-exports and persisted .vbcm interfaces.
INVALID_LIFETIME_CONCURRENCY_CONTRACTS = {
    'mutable_trait_reconstruction': ('advance("bad")\n', 'no overloads'),
    'mutable_trait_through_variant': ('Wrap.Present("bad")\n', 'no overloads'),
    'mutable_generic_object_in_dictionary': ('mutate("bad")\n', 'no overloads'),
    'mutable_generic_object_in_list': ('mutate("bad")\n', 'no overloads'),
    'trait_duplication_through_generic': ('copySize("bad")\n', 'no overloads'),
    'trait_duplication_from_union': ('duplicate("bad")\n', 'no overloads'),
    'generic_trait_move': ('forward("bad")\n', 'no overloads'),
    'stack_swap_imported_enum': ('Code.\\MISSING.value\n', 'unknown element'),
    'stack_dup_generic_box': ('$wrong:Box[Int]=Box("bad")\n', 'cannot assign'),
    'stack_pop_tagged_generic': ('@N@ #unknown\n', 'unknown data tag'),
    'spawn_generic_object_field': ('read("bad")\n', 'no overloads'),
    'spawn_trait_dynamic_dispatch': ('measure("bad")\n', 'no overloads'),
    'spawn_variant_match': ('select("bad")\n', 'no overloads'),
    'spawn_enum_overload': ('select("bad")\n', 'no overloads'),
    'spawn_trait_default': ('labelOf("bad")\n', 'no overloads'),
    'spawn_generic_higher_order': ('apply("bad",Box(@N@))\n', 'no overloads'),
    'spawn_tagged_value': ('@N@ #unknown\n', 'unknown data tag'),
    'channel_generic_object_payload': ('transport("bad")\n', 'no overloads'),
    'channel_trait_payload': ('transport("bad")\n', 'no overloads'),
    'channel_enum_payload': ('transport("bad")\n', 'no overloads'),
    'channel_variant_payload': ('transport("bad")\n', 'no overloads'),
    'channel_generic_payload': ('@N@ #unknown\n', 'unknown data tag'),
    'borrowed_nodup_method_repetition': ('inspect("bad")\n', 'no overloads'),
    'borrowed_nodup_generic_repetition': ('inspect("bad")\n', 'no overloads'),
    'borrowed_nodup_trait_dispatch': ('inspect("bad")\n', 'no overloads'),
    'borrowed_nodup_stack_permutation': ('inspect("bad")\n', 'no overloads'),
    'borrowed_nodup_tag_view': ('inspect("bad")\n', 'no overloads'),
    'borrowed_nodup_record_read': ('inspect("bad")\n', 'no overloads'),
}
assert set(INVALID_LIFETIME_CONCURRENCY_CONTRACTS) == set(LIFETIME_CONCURRENCY_CONTRACTS)
