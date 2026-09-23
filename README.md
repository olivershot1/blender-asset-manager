Model Library Shelf

A Blender add-on that gives you your own personal library of models and materials, browsable from a panel at the bottom of the 3D Viewport, with textures saved in and previews rendered automatically.

Installing it
Download model_library_shelf.py.
In Blender, go to Edit → Preferences → Add-ons.
Click Install from Disk, pick the file, and enable it.
If you're updating an existing copy, remove the old version first (uninstall it in the Add-ons list), then install the new file.

By default your library lives at:

C:\Users\<you>\BlenderModelLibrary

You can change this in the add-on's preferences (Preferences → Add-ons → Model Library Shelf → expand it → Library Folder).

Opening the library

Press Ctrl+Shift+L anywhere in the 3D Viewport. This splits your viewport and opens Blender's built-in Asset Browser at the bottom, already pointed at your library. Press the same keys again to close it.

The Asset Browser also auto-configures itself the first time it's opened (even if you open one manually via the editor-type dropdown instead of the hotkey):

It switches to your My Models library.
Import Method is set to Append with instancing switched off, so dragging something into the viewport gives you real, ready-to-edit objects — like an FBX import — instead of a linked instance.
Saving things into your library

All of these buttons live in the Asset Browser's header once it's open.

Save Selected (models)

Select one or more objects in the viewport (parented children, like meshes under an armature, are included automatically), then click Save Selected. You'll be asked for:

Name — what to call it.
Folder — type a folder path like Props/Industrial to file it away (see Folders below). Leave empty to save it unsorted.
Add Into — instead of making a new file, you can add this model into one of your existing library files, so related variants live together.

Textures used by the model are packed in automatically, and a preview is rendered from the model itself (with its textures) in the background — this takes a few seconds.

Save Material(s)

Select an object that already has a material on it (built from your PNGs, however you made it) and click Save Material(s) in the 3D Viewport's N-panel (Model Library tab). It detects every material on the selected object(s) automatically — no need to open an Image Editor. If an object has more than one material, each one is saved separately in one go. You'll get the same Folder field, plus a Preview Shape dropdown (see below).

Save Texture (from an Image Editor)

If you'd rather work from a texture directly, open it in an Image Editor (e.g. the UV Editing tab) and click Save Texture in that editor's header. This wraps the image in a simple material (Blender can't show raw images in the Asset Browser, only materials/collections/etc., so this is what makes it browsable and drag-and-drop-able onto objects later).

Preview Shape

Textures and materials are shown as a lit sphere by default, like Blender's own material preview balls. When saving, pick Sphere, Cube, or Plane from the dropdown. You can also change it afterwards: select a saved material/texture asset in the browser and click one of the three shape icons in the Preview: row of the header to re-render its thumbnail.

Folders

Folders are real folders inside your library folder on disk — not just internal Blender tags — so you can also see and manage them in Explorer.

New Folder — creates a folder (optionally nested inside another) and registers it immediately.
Move to Folder — select one or more assets in the browser, click this, and pick (or type) a folder. The actual files are physically moved into that folder.
Delete Folder (the trash icon) — pick a folder and it's deleted both on disk and from Blender's list. If it still has files in it, you'll be asked to confirm before anything is deleted.
Detect Folders (the refresh icon) — click this any time you've changed things outside Blender: created a folder in Explorer, moved a .blend file into a folder yourself, or deleted a folder from disk. It re-scans everything and:
Registers any folder it finds that Blender doesn't know about yet.
Removes any folder from Blender's list that no longer exists on disk.
Fixes up any .blend file whose internal folder tag doesn't match the folder it's physically sitting in.

Since folders on disk are the source of truth, if something ever looks out of sync, Detect Folders is the fix.

Where things are, in short
Thing you did	What happens
Save Selected / Save Material(s)	New .blend (+ .png preview) in the library, in the folder you chose
Move to Folder	Existing files physically relocated
New Folder	Real folder created on disk + registered
Delete Folder	Real folder (and optionally its files) deleted
Detect Folders	Blender's folder list re-synced to match disk
Dragging an asset into the viewport	Real, editable objects (Append, no instancing)
If something goes wrong

The add-on does some of its work (rendering previews, applying folders) using a background copy of Blender that runs invisibly for a few seconds. If a save or move doesn't seem to do anything, open Window → Toggle System Console (Windows only) before trying again — it will print exactly what that background step did or where it failed, which is the fastest way to work out what's wrong. If you do run into a issue please make a issues ticket and ill fix it asap. 