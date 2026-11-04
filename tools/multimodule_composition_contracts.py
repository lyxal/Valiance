"""Connected cross-owner compositions, with independent seed-based value oracles.

Each contract moves a value or a type across provider, helper, and consumer.
Labels describe operations *on that value*, not incidental file contents.
Syntax is intentionally literal: failing grammar/type variants are not counted.
"""

COMPOSITION_CONTRACTS = {
    "enum_in_generic_box": (
        ("enum", "generic", "generic_specialisation", "object", "construction", "field", "nominal"),
        'public enum[String] Token =>\n YES = "yes"\n NO = "no"\nend\n', ("Token",),
        'public object[T] Box => public $value:T end\n', ("Box",),
        '$box=Box(Token.\\YES)\n$token=$box.value\n$token.value\n', ("yes",)),
    "enum_in_generic_variant": (
        ("enum", "variant", "generic", "generic_specialisation", "match", "nominal"),
        'public enum[String] Token =>\n YES = "yes"\n NO = "no"\nend\n', ("Token",),
        'public variant[T] Choice =>\n Present => public $value:T end\n Empty => end\nend\n', ("Choice",),
        'Choice.Present(Token.\\YES) match =>\n as :Present(token) => $token.value\n as :Empty => "no"\nend\n', ("yes",)),
    "enum_through_list": (
        ("enum", "finite_list", "generic", "element", "nominal", "type_inference"),
        'public enum[Int] Code => GOOD = @N@ end\n', ("Code",),
        'public define[T] select(values:T+ novec)->T=>$values[0] end\n', ("select",),
        '$code=select([Code.\\GOOD,Code.\\GOOD])\n$code.value\n', ("@N@",)),
    "enum_through_dictionary": (
        ("enum", "dictionary", "generic", "element", "nominal", "type_inference"),
        'public enum[Int] Code => GOOD = @N@ end\n', ("Code",),
        'public define[T] identity(value:T)->T=>$value end\n', ("identity",),
        '$map=identity(dict{"code"=>Code.\\GOOD})\n$code=$map["code"]\n$code.value\n', ("@N@",)),
    "enum_through_record": (
        ("enum", "record", "generic", "element", "field", "type_inference"),
        'public enum[Int] Code => GOOD = @N@ end\n', ("Code",),
        'public define[T] identity(value:T)->T=>$value end\n', ("identity",),
        '$row=identity(record{code=>Code.\\GOOD})\n$code=$row.code\n$code.value\n', ("@N@",)),
    "enum_through_tuple": (
        ("enum", "tuple", "generic", "element", "type_inference", "nominal"),
        'public enum[Int] Code => GOOD = @N@ end\n', ("Code",),
        'public define[T] identity(value:T)->T=>$value end\n', ("identity",),
        '$pair=identity({Code.\\GOOD,Code.\\GOOD})\n$code=$pair[1]\n$code.value\n', ("@N@",)),
    "variant_stored_in_object": (
        ("variant", "object", "field", "construction", "match", "nominal"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic object Holder => public $choice:Choice end\n', ("Holder",),
        '$h=Holder(Choice.Present(@N@))\n$h.choice match =>\n as :Present(v) => $v\n as :Empty => -1\nend\n', ("@N@",)),
    "variant_through_tuple": (
        ("variant", "tuple", "generic", "match", "type_inference", "element"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'public define[T] identity(value:T)->T=>$value end\n', ("identity",),
        '$pair=identity({Choice.Present(@N@),Choice.Present(0)})\n$pair[0] match =>\n as :Present(v) => $v\n as :Empty => -1\nend\n', ("@N@",)),
    "variant_through_record": (
        ("variant", "record", "generic", "match", "field", "type_inference"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'public define[T] identity(value:T)->T=>$value end\n', ("identity",),
        '$r=identity(record{choice=>Choice.Present(@N@)})\n$r.choice match =>\n as :Present(v) => $v\n as :Empty => -1\nend\n', ("@N@",)),
    "variant_in_option": (
        ("variant", "option", "match", "generic", "construction", "type_inference"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic define[T] identity(value:T)->T=>$value end\npublic define inspect(value:Choice?)->Int =>\n $x=identity($value)\n $x match =>\n  as :Some(choice) =>\n   $choice match =>\n    as :Present(v) => $v\n    as :Empty => -1\n   end\n  as :None => -2\n end\nend\n', ('inspect',),
        'inspect(Some(Choice.Present(@N@)))\n', ("@N@",)),
    "variant_in_list_mutation": (
        ("variant", "finite_list", "ownership", "match", "construction", "element"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic define update(value:Choice)->Choice=>\n $xs=[Choice.Present(0),Choice.Present(0)]\n $xs[0]=$value\n $xs[0]\nend\n', ("update",),
        'update(Choice.Present(@N@)) match =>\n as :Present(v) => $v\n as :Empty => -1\nend\n', ("@N@",)),
    "variant_in_dictionary_mutation": (
        ("variant", "dictionary", "ownership", "match", "construction", "element"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic define update(value:Choice)->Choice=>\n $xs=dict{"x"=>Choice.Present(0)}\n $xs["x"]=$value\n $xs["x"]\nend\n', ("update",),
        'update(Choice.Present(@N@)) match =>\n as :Present(v) => $v\n as :Empty => -1\nend\n', ("@N@",)),
    "trait_default_via_bounded_generic": (
        ("trait", "trait_default", "generic", "generic_bounds", "object", "dispatch", "method"),
        'public trait Named => public define label->String=>"default" end end\npublic object Item => public $value:Int end\nobject Item as Named=>end\n', ("Named", "Item"),
        'import {core.Named}\npublic define[T:Named] labelOf(x:T)->String=>$x label end\n', ("labelOf",),
        'labelOf(Item(@N@))\n', ("default",)),
    "trait_inherited_default_in_variant": (
        ("trait", "trait_default", "trait_inheritance", "variant", "match", "dispatch", "object", "method"),
        'public trait Parent =>\n extend size -> Int\n public define doubled -> Int => ($self size)*2 end\nend\npublic trait Child as Parent => end\npublic object Item => public $value:Int end\nobject Item as Child => define size => $self.value end\n', ("Parent","Child","Item"),
        'import {core.Item}\npublic variant Choice =>\n Present => public $value:Item end\n Empty => end\nend\n', ("Choice",),
        'Choice.Present(Item(@N@)) match =>\n as :Present(item) => $item as[Parent] | doubled\n as :Empty => -1\nend\n', ("@N_DOUBLE@",)),
    "trait_dispatch_in_higher_order": (
        ("trait", "object", "method", "dispatch", "higher_order", "element", "field"),
        'public trait Sized=>extend size->Int end\npublic object Item=>public $value:Int end\nobject Item as Sized=>define size=>$self.value end\n', ("Sized","Item"),
        'import {core.Item}\npublic define apply(f:Function[Item -> Int],x:Item)->Int=>$f($x) end\n', ("apply",),
        'apply(fn(x:Item)->Int=>$x as[Sized] | size end,Item(@N@))\n', ("@N@",)),
    "trait_dispatch_after_option": (
        ("trait", "object", "option", "match", "dispatch", "casts", "nominal"),
        'public trait Sized=>extend size->Int end\npublic object Item=>public $value:Int end\nobject Item as Sized=>define size=>$self.value end\n', ("Sized","Item"),
        'import {core.[Sized,Item]}\npublic define inspect(x:Item?)->Int =>\n $x match =>\n  as :Some(v) => $v as[Sized] | size\n  as :None => -1\n end\nend\n', ("inspect",),
        'inspect(Some(Item(@N@)))\ninspect(\\None)\n', ("@N@", "-1")),
    "trait_dispatch_from_list": (
        ("trait", "object", "finite_list", "dispatch", "field", "method"),
        'public trait Sized=>extend size->Int end\npublic object Item=>public $value:Int end\nobject Item as Sized=>define size=>$self.value end\n', ("Sized","Item"),
        'import {core.[Sized,Item]}\npublic define inspect(items:Item+ novec)->Int=>$items[0] as[Sized] | size end\n', ("inspect",),
        'inspect([Item(@N@),Item(0)])\n', ("@N@",)),
    "trait_dispatch_from_dictionary": (
        ("trait", "object", "dictionary", "dispatch", "method", "construction"),
        'public trait Sized=>extend size->Int end\npublic object Item=>public $value:Int end\nobject Item as Sized=>define size=>$self.value end\n', ("Sized","Item"),
        'import {core.[Sized,Item]}\npublic define inspect(item:Item)->Int=>\n $m=dict{"x"=>$item}\n $m["x"] as[Sized] | size\nend\n', ("inspect",),
        'inspect(Item(@N@))\n', ("@N@",)),
    "trait_default_overload_from_enum": (
        ("trait", "trait_default", "overload", "enum", "object", "dispatch", "strings"),
        'public trait Named=>public define label->String=>"default" end end\npublic object Item=>public $value:Int end\nobject Item as Named=>end\n', ("Named","Item"),
        'import {core.[Named,Item]}\npublic enum[Int] Code => GOOD = @N@ end\npublic define show(x:Item)->String=>$x as[Named] | label end\npublic define show(x:Code)->Int=>$x.value+1 end\n', ('show', 'Code'),
        'show(Item(@N@))\nshow(Code.\\GOOD)\n', ('default', '@N_PLUS_ONE@')),
    "union_object_list_match": (
        ("union", "object", "finite_list", "match", "nominal", "construction"),
        'public object Item=>public $value:Int end\npublic object Other=>public $value:Int end\n', ("Item","Other"),
        'import {core.[Item,Other]}\npublic define inspect(v:Item|Other)->Int =>\n $v match =>\n  as item:Item => $item.value\n  as other:Other => $other.value\n end\nend\n', ("inspect",),
        '$xs=[Item(@N@),Other(0)]\ninspect($xs[0])\n', ("@N@",)),
    "union_object_dictionary_match": (
        ("union", "object", "dictionary", "match", "nominal", "construction"),
        'public object Item=>public $value:Int end\npublic object Other=>public $value:Int end\n', ("Item","Other"),
        'import {core.[Item,Other]}\npublic define inspect(v:Item|Other)->Int =>\n $v match =>\n  as item:Item => $item.value\n  as other:Other => $other.value\n end\nend\n', ("inspect",),
        '$m=dict{"x"=>Item(@N@)}\ninspect($m["x"])\n', ("@N@",)),
    "union_optional_cast_through_generic": (
        ("union", "option", "casts", "generic", "object", "match", "type_inference"),
        'public object Item=>public $value:Int end\npublic object Other=>public $value:Int end\n', ("Item","Other"),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$x=identity(Item(@N@) as[Item|Other])\n$x as?[Item] match =>\n as :Some(v) => $v.value\n as :None => -1\nend\n', ("@N@",)),
    "tuple_of_generic_boxes": (
        ("tuple", "generic", "generic_specialisation", "object", "field", "construction"),
        'public object[T] Box=>public $value:T end\n', ("Box",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$pair=identity({Box(@N@),Box("@LABEL@")})\n$pair[0].value\n$pair[1].value\n', ("@N@","@LABEL@")),
    "record_of_generic_boxes": (
        ("record", "generic", "generic_specialisation", "object", "field", "construction"),
        'public object[T] Box=>public $value:T end\n', ("Box",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$r=identity(record{first=>Box(@N@),second=>Box("@LABEL@")})\n$r.first.value\n$r.second.value\n', ("@N@","@LABEL@")),
    "dictionary_of_generic_boxes": (
        ("dictionary", "generic", "generic_specialisation", "object", "field", "construction"),
        'public object[T] Box=>public $value:T end\n', ("Box",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$m=identity(dict{"a"=>Box(@N@)})\n$m["a"].value\n', ("@N@",)),
    "generic_specialization_overload_tuple": (
        ("overload", "tuple", "generic", "generic_specialisation", "element", "strings"),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        'public define select(x:Int)->Int=>$x+1 end\npublic define select(x:String)->String=>$x end\n', ("select",),
        '$pair=identity({@N@,"@LABEL@"})\nselect($pair[0])\nselect($pair[1])\n', ("@N_PLUS_ONE@","@LABEL@")),
    "generic_specialization_overload_record": (
        ("overload", "record", "generic", "generic_specialisation", "element", "field"),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        'public define select(x:Int)->Int=>$x+1 end\npublic define select(x:String)->String=>$x end\n', ("select",),
        '$r=identity(record{i=>@N@,s=>"@LABEL@"})\nselect($r.i)\nselect($r.s)\n', ("@N_PLUS_ONE@","@LABEL@")),
    "generic_option_overload": (
        ("overload", "option", "generic", "match", "element", "type_inference"),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        'public define unpack(x:Int?)->Int =>\n $x match =>\n  as :Some(v) => $v\n  as :None => -1\n end\nend\n', ("unpack",),
        'unpack(identity(Some(@N@)))\nunpack(identity(\\None))\n', ("@N@","-1")),
    "generic_variant_in_loop": (
        ("variant", "generic", "loop", "branch", "match", "element"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic define repeat(x:Choice)->Int =>\n $i:Int=0\n $total:Int=0\n while ($i < 3) =>\n  $x match =>\n   as :Present(v) => $total := + $v\n   as :Empty => 0\n  end\n  $i := + 1\n end\n $total\nend\n', ("repeat",),
        'repeat(Choice.Present(@N@))\n', ("@N_TRIPLE@",)),
    "enum_in_branch_loop": (
        ("enum", "branch", "loop", "numbers", "generic", "element"),
        'public enum[Int] Code=>GOOD = @N@ end\n', ("Code",),
        'import {core.Code}\npublic define[T] count(x:T)->Int=>\n $i=0\n while ($i < $x.value)=>$i := + 1 end\n $i\nend\n', ("count",),
        'count(Code.\\GOOD)\n', ("@N@",)),
    "variant_in_exception_handler": (
        ("variant", "exception", "match", "element", "construction"),
        'public variant Choice =>\n Present => public $value:Int end\n Empty => end\nend\n', ("Choice",),
        'import {core.Choice}\npublic define safe(x:Choice)->Int =>\n try =>\n  $x match =>\n   as :Present(v) => $v\n   as :Empty => -1\n  end\n handle => -2 end ?!\nend\n', ("safe",),
        'safe(Choice.Present(@N@))\n', ("@N@",)),
    "tag_on_enum_via_generic": (
        ("tag", "computed", "enum", "generic", "element", "nominal"),
        'public tag #marked as computed\n', ("#marked",),
        'public enum[Int] Code=>GOOD = @N@ end\npublic define[T] identity(v:T)->T=>$v end\n', ("Code","identity"),
        'identity(Code.\\GOOD.value) #marked | #-marked\n', ("@N@",)),
    "constructed_tag_through_tuple": (
        ("tag", "constructed", "tuple", "generic", "element", "type_inference"),
        'public tag #sticky as constructed\n', ("#sticky",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$pair=identity({@N@ #sticky,@N_PLUS_ONE@ #sticky})\n$pair[0] | #-sticky\n', ("@N@",)),
    "constructed_tag_through_record": (
        ("tag", "constructed", "record", "generic", "field", "type_inference"),
        'public tag #sticky as constructed\n', ("#sticky",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$r=identity(record{value=>@N@ #sticky})\n$r.value | #-sticky\n', ("@N@",)),
    "computed_tag_through_dictionary": (
        ("tag", "computed", "dictionary", "generic", "element", "type_inference"),
        'public tag #marked as computed\n', ("#marked",),
        'public define[T] identity(v:T)->T=>$v end\n', ("identity",),
        '$m=identity(dict{"v"=>@N@ #marked})\n$m["v"] | #-marked\n', ("@N@",)),
    "unit_tag_through_generic_object": (
        ("tag", "unit", "generic", "object", "field", "construction"),
        'public tag #metres as unit\n', ("#metres",),
        'public object[T] Box=>public $value:T end\n', ("Box",),
        '$box=Box(@N@ #metres)\n$box.value | #-metres\n', ("@N@",)),
    "validator_on_object_field": (
        ("tag", "tag_validator", "computed", "object", "field", "construction", "numbers"),
        'public tag #valid as computed\npublic define #valid(v:Int)->#boolean Number=>$v>0 end\n', ("#valid",),
        'public object Item=>public $value:Int end\n', ("Item",),
        '$item=Item(@N@)\n$item.value #valid | #-valid\n', ("@N@",)),
    "overlay_on_object_field": (
        ("tag", "tag_overlay", "constructed", "object", "field", "construction", "numbers"),
        'public tag #sticky as constructed\npublic define increment(v:Number)->Number=>$v+1 end\n#sticky: increment =>\n (#sticky Number)->#sticky Number\nend\n', ("#sticky","increment"),
        'public object Item=>public $value:Int end\n', ("Item",),
        '$item=Item(@N@)\n$item.value #sticky | increment | #-sticky\n', ("@N_PLUS_ONE@",)),
    "reflection_generic_object": (
        ("reflection", "generic", "generic_specialisation", "object", "field", "nominal"),
        'public object[T] Box=>public $value:T end\n', ("Box",),
        'import {core.Box}\npublic define read(x:Box[Int])->Int=>$x[fields[Box[Int]]["value"]] end\n', ("read",),
        'read(Box(@N@))\n', ("@N@",)),
    "json_enum_value": (
        ("json", "enum", "strings", "element", "generic", "nominal"),
        'public enum[Int] Code => GOOD = @N@ end\n', ('Code',),
        'public object Holder => public $value:Int end\n', ('Holder',),
        'import {std.json}\njson.encode(Holder(Code.\\GOOD.value)) ?!\n', ('{"value":@N@}',)),
    "lazy_enum_list": (
        ("lazy_list", "finite_list", "higher_order", "enum", "nominal", "element"),
        'public enum[Int] Code=>GOOD = @N@ end\n', ("Code",),
        'import {core.Code}\npublic define select(xs:Code+ novec)->Int=>\n $ys=$xs map: fn(x:Code)->Code=>$x end\n $ys[0].value\nend\n', ("select",),
        'select([Code.\\GOOD,Code.\\GOOD])\n', ("@N@",)),
    "lazy_object_trait_dispatch": (
        ("lazy_list", "finite_list", "higher_order", "object", "trait", "dispatch", "method"),
        'public trait Sized=>extend size->Int end\npublic object Item=>public $value:Int end\nobject Item as Sized=>define size=>$self.value end\n', ("Sized","Item"),
        'import {core.[Sized,Item]}\npublic define select(xs:Item+ novec)->Int=>\n $ys=$xs map: fn(x:Item)->Int=>$x as[Sized] | size end\n $ys[0]\nend\n', ("select",),
        'select([Item(@N@),Item(0)])\n', ("@N@",)),
}
