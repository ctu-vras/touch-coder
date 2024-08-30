Workflow:

1:User extracts the zip file into folder
2:User gets video and frames
	Frames have to be in Labeled_data/name_of_video/frames
	If not frames will be generated from loaded video (takes very very long time 	but should work)
3: User opens LabelingApplicaton.exe clicks on Load Video and chooses the video to 	label
4:After that user can move to different frames using left, right arrows on keyboard, 	mousewheel or buttons next to current frame number. Or by clicking on the 	timeline 2 or timeline 1.
5: user can click on the Cloth button to select zones that are covered with clothes 	(it will be saved by closing the Cloth window)
6: user can choose limb by radio button under the diagram
7: user can code touches by clicking on the diagram
	start of the touch by left click (green dot)
	end of the touch by right click (red dot)
	middle click on the dot removes the touch
	It should be visible on the timeline (should turn yellow) 
8: From time to time user can save the labeled touches by Save button (in newer version take a bit time especially for more touches, so be patient please)
9: User can close application and it will automatically save the data


Update version 3:
User can now move to specific frame by writing the number into the entery bar and clicking Select Frame.

User can on each frame enter if the infant is looking, not looking or user doesn't know. For each touch is enough to enter just once. The state is indicated by green coloring of the corresponding button

Faster saving, ghost touch


Notes:
User can change "small" to "large" in the config file and it will make the diagram 2x the size

If you are having performance isues consider making the application smaller because if the pictures are smaller it will load them faster

User can check the data, notes and clothes in the Labeled_data/name_of_video/data folder (do not open .csv during labeling)

user can enter notes using the place under the diagram and Save Note button

Timeline "yellowing" might not work every time, the touch exist if there are the green and red dots
After labeling one video do not open new video, close the app and open again to label new video please

If you go to fast or load new section of the video red label "Loading" should appear instead of green "Loaded" just wait a bit please

In this version black terminal opens up with the application do not close it, if you experience some bug, please describe it to me and send me picture of the terminal window (there might be some error message that will help me debug)
 
Try not to click on the black lines in the diagram