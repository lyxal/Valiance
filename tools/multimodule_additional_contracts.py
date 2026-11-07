"""Additional *connected* multi-owner semantic contracts.

Each entry is (feature labels, provider source, exported provider names,
consumer-library source, exported library names, main expression, exact oracle).
Only include a feature label when it actively participates in the data/type flow.
The last field is a Python oracle, never a VM-to-VM comparison.
"""

ADDITIONAL_CONTRACTS = {
    "inherited_default_enum": (
        ("trait", "trait_default", "trait_inheritance", "dispatch", "enum", "object", "method", "field"),
        "public trait Parent =>\n extend size -> Int\n public define doubled -> Int => ($self size)*2 end\nend\n"
        "public trait Child as Parent => end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Child => define size => $self.value end\n",
        ("Parent", "Child", "Item"),
        "public variant Code =>\n GOOD = @N@\nend\n", ("Code",),
        "Item(Code.\\GOOD.value) as[Parent] | doubled\n", ("@N_DOUBLE@",),
    ),
    "bounded_generic_trait": (
        ("generic", "generic_bounds", "trait", "object", "dispatch", "element", "nominal"),
        "public trait Sized => extend size -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Sized => define size => $self.value end\n", ("Sized", "Item"),
        "import {core.Sized}\npublic define[T:Sized] measure(value:T)->Int=>$value size end\n", ("measure",),
        "measure(Item(@N@))\n", ("@N@",),
    ),
    "union_object_match": (
        ("union", "object", "match", "nominal", "field", "element"),
        "public object Item => public $value:Int end\n"
        "public object Other => public $value:Int end\n", ("Item", "Other"),
        "import {core.[Item,Other]}\n"
        "public define inspect(value:Item|Other)->Int =>\n"
        " $value match =>\n as item:Item=>$item.value\n as other:Other=>$other.value\n end\nend\n", ("inspect",),
        "inspect(Item(@N@))\ninspect(Other(@N_PLUS_ONE@))\n", ("@N@", "@N_PLUS_ONE@"),
    ),
    "optional_cast_union": (
        ("casts", "option", "union", "object", "match", "nominal"),
        "public object Item => public $value:Int end\n"
        "public object Other => public $value:Int end\n", ("Item", "Other"),
        "import {core.[Item,Other]}\n"
        "public define extract(value:Item|Other)->Int =>\n"
        " $value as?[Item] match =>\n as :Some(item)=>$item.value\n as :None=> -1\n end\nend\n", ("extract",),
        "extract(Item(@N@))\nextract(Other(@N@))\n", ("@N@", "-1"),
    ),
    "asserted_cast_trait": (
        ("casts", "trait", "object", "dispatch", "field", "nominal"),
        "public trait Sized => extend size -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Sized => define size => $self.value end\n", ("Sized", "Item"),
        "import {core.[Sized,Item]}\n"
        "public define extract(value:Sized)->Int => $value as![Item] | $.value end\n", ("extract",),
        "extract(Item(@N@))\n", ("@N@",),
    ),
    "higher_order_generic_object": (
        ("higher_order", "generic", "generic_specialisation", "object", "field", "construction", "element"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\n"
        "public define[T] apply(f:Function[T -> T], value:T)->T=>$f($value) end\n", ("apply",),
        "$f=fn(x:Box[Int])->Box[Int]=>Box($x.value+1) end\n $result=apply($f,Box(@N@))\n$result.value\n", ("@N_PLUS_ONE@",),
    ),
    "structural_record_generic": (
        ("record", "generic", "generic_bounds", "field", "element", "type_inference"),
        "public define[T,U] getX(value:T(.x:U))->U=>$value.x end\n", ("getX",),
        "import {core.getX}\n"
        "public define[T] lift(value:T(.x:Int))->Int => getX($value)+1 end\n", ("lift",),
        "lift(record{x=>@N@})\n", ("@N_PLUS_ONE@",),
    ),
    "record_object_field": (
        ("record", "object", "field", "construction", "element"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define read(value:Item)->Int => record{x=>$value.value}.x end\n", ("read",),
        "read(Item(@N@))\n", ("@N@",),
    ),
    "dictionary_object_field": (
        ("dictionary", "object", "field", "element", "construction"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define read(value:Item)->Int => dict{\"x\"=>$value.value} $[\"x\"] end\n", ("read",),
        "read(Item(@N@))\n", ("@N@",),
    ),
    "list_variant_match": (
        ("finite_list", "variant", "match", "construction", "element"),
        "public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n", ("Choice",),
        "import {core.Choice}\n"
        "public define read(value:Choice)->Int =>\n"
        " $values=[$value,$value]\n"
        " $values[0] match=>\n as :Present(v)=>$v\n as :Empty=> -1\n end\nend\n", ("read",),
        "read(Choice.Present(@N@))\n", ("@N@",),
    ),
    "dictionary_variant_match": (
        ("dictionary", "variant", "match", "construction", "element"),
        "public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n", ("Choice",),
        "import {core.Choice}\n"
        "public define read(value:Choice)->Int =>\n"
        " $values=dict{\"x\"=>$value}\n"
        " $values[\"x\"] match=>\n as :Present(v)=>$v\n as :Empty=> -1\n end\nend\n", ("read",),
        "read(Choice.Present(@N@))\n", ("@N@",),
    ),
    "optional_generic_object": (
        ("option", "generic", "object", "match", "construction", "field"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\n"
        "public define read(value:Box[Int]?)->Int =>\n"
        " $value match=>\n as :Some(box)=>$box.value\n as :None=> -1\n end\nend\n", ("read",),
        "read(Some(Box(@N@)))\nread(\\None)\n", ("@N@", "-1"),
    ),
    "tuple_generic_object": (
        ("tuple", "generic", "object", "field", "construction", "element"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\n"
        "public define read(value:Box[Int])->Int =>\n"
        " $pair={$value,$value}\n $pair[0].value\nend\n", ("read",),
        "read(Box(@N@))\n", ("@N@",),
    ),
    "branch_enum_generic": (
        ("enum", "branch", "generic", "element", "strings", "nominal"),
        "public variant Code =>\n GOOD = @N@\n BAD = -1\nend\n", ("Code",),
        "public define[T] choose(x:T)->T=>$x end\n", ("choose",),
        "if (Code.\\GOOD.value > 0) => choose(Code.\\GOOD.value) + 1\n"
        "else => 0 end\n", ("@N_PLUS_ONE@",),
    ),
    "while_generic_object": (
        ("loop", "branch", "generic", "object", "field", "element"),
        "public object[T] Box => public $value:T end\n", ("Box",),
        "import {core.Box}\n"
        "public define count(value:Box[Int])->Int =>\n"
        " $i:Int=0\n while ($i < $value.value) => $i := + 1 end\n $i\nend\n", ("count",),
        "count(Box(@N@))\n", ("@N@",),
    ),
    "exception_trait_dispatch": (
        ("exception", "trait", "dispatch", "object", "method", "element"),
        "public trait Sized => extend size -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Sized => define size => $self.value end\n", ("Sized", "Item"),
        "import {core.[Sized,Item]}\n"
        "public define safe(value:Item)->Int =>\n"
        " try => $value as[Sized] | size\n handle => -1 end ?!\nend\n", ("safe",),
        "safe(Item(@N@))\n", ("@N@",),
    ),
    "higher_order_object_method": (
        ("higher_order", "method", "object", "field", "element"),
        "public object Item =>\n public $value:Int\n"
        " public define read -> Int => $self.value end\nend\n", ("Item",),
        "import {core.Item}\n"
        "public define apply(f:Function[Item -> Int], value:Item)->Int=>$f($value) end\n", ("apply",),
        "apply(fn(x:Item)->Int=>$x read end,Item(@N@))\n", ("@N@",),
    ),
    "unit_tag_enum": (
        ("tag", "unit", "enum", "generic", "element", "nominal"),
        "public tag #metres as unit\n", ("#metres",),
        "public variant Distance => FAR = @N@ end\n"
        "public define[T] identity(x:T)->T=>$x end\n", ("Distance", "identity"),
        "Distance.\\FAR.value #metres | #-metres | identity\n", ("@N@",),
    ),
    "variant_tag_trait": (
        ("tag", "computed", "trait", "object", "dispatch", "match"),
        "public tag #sorted as computed\npublic tag #ascending as #sorted\n", ("#sorted", "#ascending"),
        "public trait Sized => extend size -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Sized => define size => $self.value end\n", ("Sized", "Item"),
        "(Item(@N@) as[Sized] | size) #ascending match=>\n"
        " as :#ascending Int=>1\n _=>0\nend\n", ("1",),
    ),
    "overload_trait_default": (
        ("overload", "trait_default", "trait", "dispatch", "object", "element"),
        "public trait Named => public define label->String=>\"default\" end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Named => end\n", ("Named", "Item"),
        "import {core.[Named,Item]}\n"
        "public define describe(x:Int)->String=>\"int\" end\n"
        "public define describe(x:Named)->String=>$x label end\n", ("describe",),
        "describe(Item(@N@))\ndescribe(@N@)\n", ("default", "int"),
    ),
    "intersection_trait_object": (
        ("intersection", "trait", "dispatch", "object", "nominal", "element"),
        "public trait Left => extend size -> Int end\n"
        "public trait Right => extend bonus -> Int end\n"
        "public object Item => public $value:Int end\n"
        "object Item as Left => define size=>$self.value end\n"
        "object Item as Right => define bonus=>1 end\n", ("Left", "Right", "Item"),
        "import {core.[Left,Right,Item]}\n"
        "public define evaluate(value:Left & Right)->Int=>($value size)+($value bonus) end\n", ("evaluate",),
        "evaluate(Item(@N@))\n", ("@N_PLUS_ONE@",),
    ),
    "list_generic_overload": (
        ("finite_list", "rank", "generic", "overload", "vectorisation", "element"),
        "public define[T] identity(value:T)->T=>$value end\n", ("identity",),
        "import {core.identity}\n"
        "public define head(value:Int+ novec)->Int=>$value[0] end\n"
        "public define head(value:Int novec)->Int=>$value end\n", ("head",),
        "head(identity([@N@,@N_PLUS_ONE@]))\nhead(identity(@N@))\n", ("@N@", "@N@"),
    ),
    "tag_validator_overload": (
        ("tag", "tag_validator", "computed", "overload", "generic", "element", "numbers"),
        "public tag #valid as computed\n"
        "public define #valid(value:Number)->#boolean Number => false end\n"
        "public define #valid(value:Int)->#boolean Number => $value > 0 end\n", ("#valid",),
        "public define[T] identity(x:T)->T=>$x end\n", ("identity",),
        "@N@ #valid | #-valid | identity\n", ("@N@",),
    ),
    "tag_overlay_generic": (
        ("tag", "tag_overlay", "constructed", "generic", "element", "numbers"),
        "public tag #sticky as constructed\n"
        "public define increment(value:Number)->Number=>$value+1 end\n"
        "#sticky: increment =>\n (#sticky Number)->#sticky Number\nend\n",
        ("#sticky", "increment"),
        "public define[T] identity(x:T)->T=>$x end\n", ("identity",),
        "@N@ #sticky | increment | #-sticky | identity\n", ("@N_PLUS_ONE@",),
    ),
    "reflected_object_field": (
        ("reflection", "object", "field", "nominal", "element"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define reflect(value:Item)->Int =>\n"
        " $field=fields[Item][\"value\"]\n"
        " $value[$field]\nend\n", ("reflect",),
        "reflect(Item(@N@))\n", ("@N@",),
    ),
    "json_object_roundtrip": (
        ("json", "object", "field", "construction", "element", "strings"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define relay(value:Item)->Item=>$value end\n", ("relay",),
        "import {std.json}\njson.encode(relay(Item(@N@))) ?!\n", ('{\"value\":@N@}',),
    ),
    "lazy_variant_map": (
        ("lazy_list", "finite_list", "higher_order", "variant", "match", "generic", "element"),
        "public variant Choice =>\n Present=> public $value:Int end\n Empty=>end\nend\n", ("Choice",),
        "import {core.Choice}\n"
        "public define select(value:Choice)->Int=>\n"
        " $xs=[$value,$value] map: fn(x:Choice)->Choice=>$x end\n"
        " $xs[0] match=>\n as :Present(v)=>$v\n as :Empty=> -1\n end\nend\n", ("select",),
        "select(Choice.Present(@N@))\n", ("@N@",),
    ),
    "list_object_mutation": (
        ("finite_list", "ownership", "object", "construction", "field", "element"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define update(value:Item)->Int =>\n"
        " $items=[Item(0),$value]\n"
        " $items[0]=Item($value.value+1)\n"
        " $items[0].value\nend\n", ("update",),
        "update(Item(@N@))\n", ("@N_PLUS_ONE@",),
    ),
    "dict_object_mutation": (
        ("dictionary", "ownership", "object", "construction", "field", "element"),
        "public object Item => public $value:Int end\n", ("Item",),
        "import {core.Item}\n"
        "public define update(value:Item)->Int =>\n"
        " $items=dict{\"k\"=>Item(0)}\n"
        " $items[\"k\"]=Item($value.value+1)\n"
        " $items[\"k\"].value\nend\n", ("update",),
        "update(Item(@N@))\n", ("@N_PLUS_ONE@",),
    ),
    "tag_variant_match": (
        ("tag", "computed", "match", "element", "nominal"),
        "public tag #sorted as computed\npublic tag #ascending as #sorted\n", ("#sorted", "#ascending"),
        "import {core.[#sorted,#ascending]}\n"
        "public define classify(value:Number)->Int =>\n"
        " $value match=>\n as :#ascending Number=>1\n _=>0\n end\nend\n", ("classify",),
        "@N@ #ascending | classify\n", ("1",),
    ),

}
