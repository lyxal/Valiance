"""Benchmark a nested-grid callback workload without hard-coding its rule."""
from __future__ import annotations
import argparse
from pathlib import Path
from time import perf_counter
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program

SOURCE = r'''
import { std.grids.allNeighbors, std.random.randbit }
define step(board: Number++) -> Number++ =>
  $board allNeighbors(wrapping = true)
  map fn (cells) =>
    [$cells[4], sum removeAt($cells, 4)] match =>
      [_, 3] => 1
      [1, 2] => 1
      default => 0
    end
  end
end
const ($WIDTH, $HEIGHT) = 10 | 10
$board = range(1, $WIDTH * $HEIGHT) | map: randbit | reshape(_, $WIDTH, $HEIGHT)
range(1, __ITERATIONS__) foreach (n) => $board := step
$board
'''

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    analyser = Analyser()
    typed = analyser.analyse(parse(SOURCE.replace("__ITERATIONS__", str(args.iterations))))
    if analyser.diagnostics:
        raise RuntimeError(analyser.diagnostics)
    program = compile_program(typed)
    started = perf_counter()
    VirtualMachine(output=lambda _value: None).run(program)
    print(f"{args.iterations} generations: {perf_counter() - started:.6f}s")

if __name__ == "__main__":
    main()
