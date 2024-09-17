# Labeling Application

## Introduction

This application is designed for behavioral researchers to code self-contact (self-touch) in videos.

## How to Use

### Step 1: Download and Extract the ZIP File

1. **Download** the ZIP file containing the application.
2. **Extract** the contents of the ZIP file into a folder on your computer.

### Step 2: Run the Application

1. Open the extracted folder.
2. Double-click on `LabelingApplication_v2_5.exe` to launch the application.

## Application Workflow

1. **Extract the ZIP File:**
   - Extract the ZIP file into a folder of your choice.

2. **Prepare Video and Frames:**
   - Ensure that your video file is in the `Videos` folder.
   - Frames should be placed in `Labeled_data/name_of_video/frames`.
   - If frames are not present, they will be automatically generated from the video, which might take a significant amount of time.

3. **Load Video:**
   - Open `LabelingApplication_version.exe`.
   - Click on "Load Video" and select the video you want to label.

4. **Navigate Through Frames:**
   - Use the left and right arrow keys, the mouse wheel, or the buttons next to the current frame number to move between frames.
   - You can also navigate by clicking on Timeline 1 or Timeline 2.

5. **Select Clothing Zones:**
   - Click the "Cloth" button to select the zones that are covered with clothes.
   - Save the selection by closing the Cloth window.

6. **Select Limb:**
   - Choose a limb using the radio buttons located under the diagram.

7. **Code Touches:**
   - Start a touch by left-clicking on the diagram (a green dot will appear).
   - End a touch by right-clicking (a red dot will appear).
   - Middle-click on a dot to remove it.
   - Touches will be indicated by a yellow color on the timeline.

8. **Save Labeled Touches:**
   - Periodically save your work by clicking the "Save" button.
   - Saving may take some time, especially with more touches, so please be patient.

9. **Close the Application:**
   - The application will automatically save your data when you close it.

## Updates in Version 3

- **Frame Navigation:**
  - Move to a specific frame by entering the frame number in the entry bar and clicking "Select Frame."

- **Infant’s Gaze:**
  - Indicate whether the infant is looking, not looking, or if you’re unsure. This only needs to be entered once per touch. The state is indicated by the green color of the corresponding button.

- **Performance Improvements:**
  - Faster saving.
  - Introduction of ghost touch.

## Additional Notes

- **Config File:**
  - You can change `"small"` to `"large"` in the config file to double the size of the diagram.

- **Performance Tips:**
  - If you experience performance issues, consider making the application window smaller. Smaller pictures will load faster.

- **Data Checking:**
  - You can check the data, notes, and clothes in the `Labeled_data/name_of_video/data` folder. Avoid opening `.csv` files during labeling.

- **Adding Notes:**
  - Enter notes in the space under the diagram and click "Save Note."

- **Timeline Issues:**
  - Sometimes, the timeline may not turn yellow as expected. However, the touch still exists if you see green and red dots.

- **Labeling Multiple Videos:**
  - After labeling one video, close the application before opening a new one.

- **Loading Indicator:**
  - If you see a red "Loading" label instead of a green "Loaded" label, just wait a bit for the section to load.

- **Terminal Window:**
  - A black terminal window will open with the application. Do not close it. If you encounter a bug, describe it and send a picture of the terminal window. It may contain error messages that will help with debugging.

- **Diagram Interaction:**
  - Avoid clicking on the black lines in the diagram.
 
  ## Related publications
  Example of analysis based on this type of coding is:
  Khoury, J., Popescu, S. T., Gama, F., Marcel, V. and Hoffmann, M. (2022), Self-touch and other spontaneous behavior patterns in early infancy, in 'IEEE International Conference on Development and Learning (ICDL)', pp. 148-155. [link to pdf](https://drive.google.com/file/d/1iVgMr-8eJFPH8jU31ksDNmv4xWY_4s5q/view?usp=sharing)
  
