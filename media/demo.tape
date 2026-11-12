# The README's demo: vhs media/demo.tape, from the repository root (vhs 0.12), then the two stills:
#   ffmpeg -y -i /tmp/uptick-demo/pane.png -vf crop=652:350:830:20 media/pane.png
#   ffmpeg -y -i /tmp/uptick-demo/band.png -vf crop=1500:124:0:676 media/band.png
# Everything shown is synthetic: media/demo/setup.sh builds the world, with fake brew, claude, npx, uv and python3.
Output media/demo.gif
Set Shell bash
Set FontSize 15
Set Width 1500
Set Height 820
Set Padding 20
Set TypingSpeed 60ms
Set Theme "Catppuccin Mocha"

Hide
Type "source media/demo/setup.sh && clear"
Enter
Sleep 2s
Show

Type "claude --plugin-dir uptick"
Sleep 500ms
Enter
Sleep 9s
Screenshot "/tmp/uptick-demo/band.png"
Sleep 1500ms
Type "/uptick"
Sleep 800ms
Enter
Sleep 3500ms
Screenshot "/tmp/uptick-demo/pane.png"
Type "3"
Sleep 2500ms
Type "n"
Sleep 1s
Type "a"
Sleep 2500ms
Shift+Tab
Sleep 1s
Enter
Sleep 12s
