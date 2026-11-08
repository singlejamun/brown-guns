# Security Patterns Folder
# ========================
# Put example scam / hacked-account messages here so the bot can detect them.
#
# SUPPORTED FILES
# ---------------
# 1) .txt files
#    - One phrase per line (case-insensitive substring match)
#    - Lines starting with # are comments (ignored)
#    - Empty lines are ignored
#
# 2) Screenshots / images  (.png  .jpg  .jpeg  .webp  .gif  .bmp)
#    - Drop a screenshot of a real scam message into this folder
#    - On bot start (or !reloadpatterns) the bot runs OCR and turns
#      the text inside the image into detection patterns
#
# HOW TO ADD A NEW PATTERN
# ------------------------
# A) Text:
#      echo "mrbeast is giving away free nitro" >> mrbeast_scam.txt
#      then: !reloadpatterns
#
# B) Screenshot:
#      copy the image into this folder, e.g. scam_ss_1.png
#      then: !reloadpatterns
#
# RUNTIME DETECTION (what the bot checks when someone posts)
# ----------------------------------------------------------
# 1. Message text + embeds
# 2. Image + suspicious caption (simple path)
# 3. OCR of any image the member attaches (strong path)
#
# After changing files in this folder, run:  !reloadpatterns
# (or restart the bot)
