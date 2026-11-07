"""Client package for the LAN collaborative whiteboard.

The network thread never touches tkinter; it pushes messages into a queue
and the UI polls it (see client/net.py and client/ui.py). This rule is
what keeps the client responsive and crash-free.
"""
