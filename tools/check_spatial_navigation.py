"""Compare the compiled BD-J engine against Python on deterministic layouts.

Requires an external JDK and the interactive BD-J API (neither is redistributed).
"""
import argparse
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from menu_navigation import nearest_button

HARNESS = r'''
import java.util.Scanner;
public class SpatialCheck {
    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);
        while (input.hasNextInt()) {
            int count = input.nextInt(), current = input.nextInt();
            String direction = input.next();
            int[][] rects = new int[count][4];
            for (int i = 0; i < count; i++)
                for (int j = 0; j < 4; j++) rects[i][j] = input.nextInt();
            System.out.println(DiscForgeXlet.nearestButton(rects, current, direction));
        }
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--api', type=Path, required=True)
    args = parser.parse_args()
    rng = random.Random(728)
    cases = []
    layouts = [[], [(0, 0, 100, 100)], [(100, 100, 100, 100), (400, 100, 100, 100),
               (100, 400, 100, 100), (400, 400, 100, 100)],
               [(0, 0, 20, 20), (30, 40, 20, 20), (50, 0, 20, 20)],
               [(0, 0, 100, 100), (10, 10, 80, 80)]]
    for _ in range(120):
        layout = []
        for _ in range(rng.randint(1, 16)):
            w, h = rng.randint(32, 1000), rng.randint(32, 400)
            layout.append((rng.randint(0, 1920-w), rng.randint(0, 1080-h), w, h))
        layouts.append(layout)
    for rects in layouts:
        for current in range(max(1, len(rects))):
            for key in ('up', 'down', 'left', 'right'):
                cases.append((rects, current, key))
    data = '\n'.join(f'{len(rs)} {i} {key} '+ ' '.join(str(v) for r in rs for v in r)
                     for rs, i, key in cases)
    expected = [nearest_button(rs, i, key) for rs, i, key in cases]
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)/'SpatialCheck.java'
        source.write_text(HARNESS, encoding='utf-8')
        cp = os.pathsep.join((str(ROOT/'assets/bdj/menu.jar'), str(args.api), tmp))
        subprocess.run([str(args.jdk/'bin/javac.exe'), '-cp', cp, str(source)], check=True)
        result = subprocess.run([str(args.jdk/'bin/java.exe'), '-Djava.awt.headless=true', '-cp', cp,
                                 'SpatialCheck'], input=data, text=True, capture_output=True, check=True)
        actual = [int(line) for line in result.stdout.splitlines()]
        if actual != expected:
            raise AssertionError('Compiled BD-J and preview navigation disagree')
    print(f'PASS: {len(cases)} direction choices match the compiled BD-J engine.')


if __name__ == '__main__':
    main()
