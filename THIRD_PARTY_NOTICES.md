# Third-party notices

DiscForge RU's application and Xlet source are MIT licensed; see LICENSE.

## Noto Sans and Noto Sans Symbols 2

Bundled font files come from https://github.com/notofonts/noto-fonts and are
distributed under the SIL Open Font License 1.1. The complete copyright and
license text is included in `assets/fonts/OFL.txt`.

## HD Cookbook binary format reference

The BDJO/index serialization in `disc_menu.py` was adapted from the file
format layouts in the HD Cookbook DiscCreationTools, principally BDJOWriter,
Index, Indexes and BDJIndexObject. Reference source:
https://github.com/cheeseb1234/auto-bluray-tui/tree/main/DiscCreationTools

Copyright (c) 2007, Sun Microsystems, Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice,
  this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.
* Neither the name of Sun Microsystems nor the names of its contributors may
  be used to endorse or promote products derived from this software without
  specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

## External programs

FFmpeg and tsMuxer run as separate executables and are not included in the
source repository or CI application artifacts. Their licenses and source
distribution requirements remain separate. IMAPI2FS and isoburn are Windows
components. A Java runtime, Eclipse ECJ and BD-J API archive are used only to
rebuild the Xlet; no API stubs or compiler classes are bundled in menu.jar.
