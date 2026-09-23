Hi!

So, first off, this isn't fully ready yet, but it's at a build where it works.

### Installation

First, add the `.py` file to Blender.

Go to **Edit > Preferences > Add-ons**. At the top, there should be a down arrow. Press that, then select **Install from Disk** and add the `.py` file.

Then press **Ctrl + Shift + L**.

You'll see a lot of stuff you don't need. Go to **All Libraries**, then click on **My Models**.

From there, click **New Folder** in the menu. This will create a new folder at:

`C:\Users\YourSystemName\BlenderModelLibrary`

For example, mine is:

`C:\Users\Oliver\BlenderModelLibrary`

From there, you can import your models into Blender.

### Saving a Model

Press **A** to select the whole model and all of its components.

Then, in the menu, press **Open** and select **Save Selected**.

This will bring up a UI where you can name the model, choose the folder, and add information about it. *(WIP)*

For now, folder names have to be entered manually. So, for me, it would be:

`test/test1`

In the future, this will be automatic, so you'll just be able to select the folder you want to save the model to.

Once you've done that, press **OK**.

Then press the **Refresh** arrow next to **My Models**.

Boom! Your model has now been added to the add-on.

Even if you delete the model from your Blender scene, it will always be saved in:

`C:\Users\YourSystemName\BlenderModelLibrary`

I'm still working on texture saving, but I should have that working within the next few days.

Enjoy!
