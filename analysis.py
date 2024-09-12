from plotly.subplots import make_subplots #bad
import plotly.express as px #bad
import plotly.graph_objs as go #bad
import pandas as pd #bad    
import base64
from PIL import Image
from collections import defaultdict
import os
import math
import webbrowser #ok
from collections import Counter  
import statistics
    

def do_analysis(folder_path,output_folder,name,debug):
    
    
    
    
    
    
    
    limbs = ['LH', 'RH', 'LL', 'RL']
    def load_and_clean_data(file_path):
        # Load the CSV file
        
        data = pd.read_csv(file_path)

        # Drop rows where both 'X' and 'Y' are NaN, but keep rows where at least one is filled
        cleaned_data = data.dropna(subset=['X', 'Y'], how='all')

        # Convert the cleaned data into a dictionary
        data_dict = cleaned_data.to_dict(orient='records')
        
        # Total number of frames
        total_frames = len(data)

        return data_dict, total_frames

    def load_all_4(folder_path,name):
        # Initialize a dictionary to store the data from each file
        data_dicts = []
        total_frames_dict = {}

        # Define the expected suffixes for each file
        suffixes = limbs

        # Iterate through each suffix and load the corresponding file
        i= 0
        for suffix in suffixes:
            
            # Construct the file name by finding the file with the appropriate suffix in the folder
            file_name = next((f for f in os.listdir(folder_path) if f.endswith(f'{name+suffix}.csv')), None)

            if file_name:
                file_path = os.path.join(folder_path, file_name)
                # Load and clean the data
                data_dict, total_frames = load_and_clean_data(file_path)
                # Store the data and frame count in the dictionary
                data_dicts.append(data_dict) 
                
            else:
                if debug:print(f"No file found for suffix: {suffix}")
            i = i + 1
        return data_dicts, total_frames

    def count_touches(data_dict):
        touch_count = 0
        ongoing_touch = False

        for entry in data_dict:
            onset = entry.get('Onset')
            
            if onset == 'On' and not ongoing_touch:
                # Start a new touch
                touch_count += 1
                ongoing_touch = True
            elif onset == 'Off' and ongoing_touch:
                # End the current touch
                ongoing_touch = False
        
        return touch_count
    # Example usage
    #folder_path = 'Labeled_data/cat3_mp4/data'  # Replace with your actual folder path
    folder_path = folder_path
    data_dicts, total_frames = load_all_4(folder_path,name)
    print("Total frames:",total_frames)
    if debug:print(data_dicts)
    # Now you can work with the `data_dict` variable for further analysis
    #print(data_dicts[1][:5])  # Display the first 5 entries as a sample

    total_touches_list = []
    touch_durations_list = []
    total_duration_list =[]
    percentage_touching_list = []
    average_touch_duration_list = []
    touch_rate_list = []
    onset_count_distribution_list = []
    zone_touch_count_list =[]
    stdev_list = []
    for i in range(len(data_dicts)):
        data_dict = data_dicts[i]
        
        
        # Example usage:
        # Assuming you have already loaded the data into `data_dict` using the previous function
        total_touches = count_touches(data_dict)
        total_touches_list.append(total_touches)
        if debug:print(f'Total number of touches: {total_touches}')

        def calculate_touch_durations(data_dict):
            touch_durations = []
            start_frame = None
            ongoing_touch = False

            for entry in data_dict:
                onset = entry.get('Onset')
                frame = entry.get('Frame')

                if onset == 'On' and not ongoing_touch:
                    # Start a new touch
                    start_frame = frame
                    ongoing_touch = True
                elif onset == 'Off' and ongoing_touch:
                    # Calculate the duration of the touch
                    duration = frame - start_frame
                    touch_durations.append(duration)
                    ongoing_touch = False

            return touch_durations

        # Example usage:
        # Assuming you have already loaded the data into `data_dict` using the previous function
        touch_durations = calculate_touch_durations(data_dict)
        
        
        touch_durations_list.append(touch_durations)
        #calculate the standart mean deviation
        if len(touch_durations) > 0:
            
            mean_value = statistics.mean(touch_durations)
        else:
            mean_value = None
        print(i,f'mean_value: {mean_value}')
# Calculate standard deviation (Population)
        if len(touch_durations) >= 2:
            std_dev = statistics.stdev(touch_durations)
            print(i,f'Stdev: {std_dev}')
        else:
            std_dev = None
        stdev_list.append(std_dev)
        total_duration = sum(touch_durations)
        total_duration_list.append(total_duration)
        if debug:
            print(f'Touch durations: {touch_durations}')
            print(f'Total durations: {total_duration}')
            print(f'Total frames: {total_frames}')
            print(f'Total number of touches: {len(touch_durations)}')
        if total_frames != 0: percentage_touching = (total_duration / total_frames) * 100
        percentage_touching_list.append(percentage_touching)
        if debug:
            print(f'Percentage of time touching: {percentage_touching:.2f}%')
        
        if len(touch_durations) > 0:
            average_touch_duration = total_duration / len(touch_durations)
        else:
            average_touch_duration = 0  # To handle cases where there are no touches
        average_touch_duration_list.append(average_touch_duration)
        if debug:print(f'Average touch duration: {average_touch_duration:.2f} frames')
        if total_frames != 0: touch_rate = 100*len(touch_durations) / total_frames
        touch_rate_list.append(touch_rate)
        if debug:print(f'Touch rate: {touch_rate:.6f} touches per frame')



        

        def count_onset_events(data_dict):
            onset_counts = []
            current_onset_count = 0
            ongoing_touch = False

            for entry in data_dict:
                onset = entry.get('Onset')

                if onset == 'On':
                    if not ongoing_touch:
                        # Starting a new touch
                        ongoing_touch = True
                        current_onset_count = 1
                    else:
                        # Continuing the same touch with another Onset
                        current_onset_count += 1
                elif onset == 'Off' and ongoing_touch:
                    # Ending the current touch
                    onset_counts.append(current_onset_count)
                    ongoing_touch = False

            # Handle case where the last touch does not have an 'Off' event
            if ongoing_touch:
                onset_counts.append(current_onset_count)

            # Count the number of touches with different onset counts
            onset_count_distribution = defaultdict(int)
            for count in onset_counts:
                onset_count_distribution[count] += 1

            return onset_count_distribution

        # Example usage:
        # Assuming you have already loaded the data into `data_dict` using the previous function
        onset_count_distribution = count_onset_events(data_dict)
        onset_count_distribution_list.append(onset_count_distribution)
        # Print the distribution
        if debug:
            for onset_count, num_touches in onset_count_distribution.items():
                print(f'Touches with {onset_count} onset(s): {num_touches}')
                
            

        

        def count_touches_per_zone(data_dict):
            zone_touch_count = defaultdict(int)
            ongoing_touch = False
            current_zones = set()

            for entry in data_dict:
                onset = entry.get('Onset')
                zones = eval(entry.get('Zones'))  # Convert string representation of list back to list

                if onset == 'On':
                    if not ongoing_touch:
                        # Start tracking zones for a new touch
                        ongoing_touch = True
                        current_zones = set(zones)
                    else:
                        # Continue tracking zones within the same touch
                        current_zones.update(zones)
                elif onset == 'Off' and ongoing_touch:
                    # When a touch ends, count all zones involved
                    for zone in current_zones:
                        zone_touch_count[zone] += 1
                    ongoing_touch = False

            # Handle case where the last touch does not have an 'Off' event
            if ongoing_touch:
                for zone in current_zones:
                    zone_touch_count[zone] += 1

            return zone_touch_count

        # Example usage:
        # Assuming you have already loaded the data into `data_dict` using the previous function
        zone_touch_count = count_touches_per_zone(data_dict)
        zone_touch_count_list.append(zone_touch_count)
        # Convert the zone touch count dictionary to a pandas DataFrame for easier viewing
        zone_touch_df = pd.DataFrame(list(zone_touch_count.items()), columns=['Zone', 'Number of Touches'])

        # Display the DataFrame as a table
        if debug:print(zone_touch_df)





        

        def create_touch_transition_matrix(data_dict):
            # Define all possible zones
            zones = [
                '1L', '2L', '3L', '4L', '5L', '6L', '7L', '8L', '9L', '10L', '11L',
                '13L', '16L', '17L', '13LB', '17LB',
                '1R', '2R', '3R', '4R', '5R', '6R', '7R', '8R', '9R', '10R', '11R',
                '13R', '16R', '17R', '13RB', '17RB','NN'
            ]
            
            # Initialize a transition matrix with all zones set to 0
            transition_matrix = pd.DataFrame(0, index=zones, columns=zones)

            ongoing_touch = False
            start_zone = None

            for entry in data_dict:
                onset = entry.get('Onset')
                zones_touched = eval(entry.get('Zones'))  # Convert string representation of list back to list
                
                # Debug print to check the current entry being processed
                if debug:print(f"Processing Frame: {entry.get('Frame')}, Onset: {onset}, Zones: {zones_touched}")

                if onset == 'On' and not ongoing_touch:
                    # Start a new touch, record the start zone
                    start_zone = zones_touched[0]  # Assuming the first zone in the list is the start zone
                    ongoing_touch = True
                    if debug:print(f"Touch started in zone: {start_zone}")
                elif onset == 'Off' and ongoing_touch:
                    # End the touch, record the end zone and update the matrix
                    end_zone = zones_touched[0]  # Assuming the first zone in the list is the end zone
                    transition_matrix.at[start_zone, end_zone] += 1
                    ongoing_touch = False
                    if debug:print(f"Touch ended in zone: {end_zone}")
                elif onset == 'Off' and not ongoing_touch:
                    if debug:print("Encountered 'Off' without an ongoing touch. Skipping.")

            return transition_matrix

        # Example usage:
        # Assuming you have already loaded the data into `data_dict` using the previous function
        transition_df = create_touch_transition_matrix(data_dict)

        # Display the transition matrix
        #print(transition_df)

        

        # Temporarily adjust display options
        pd.set_option('display.max_rows', None)    # Show all rows
        pd.set_option('display.max_columns', None) # Show all columns

        # Assuming transition_df is your DataFrame
        #print(transition_df)

        # Reset display options to default (optional)
        pd.reset_option('display.max_rows')
        pd.reset_option('display.max_columns')

        
        # Assuming you have your transition matrix in transition_df

        # Create the heatmap using Plotly
        fig = px.imshow(
            transition_df,
            labels=dict(x="End Zone", y="Start Zone", color="Number of Touches"),
            x=transition_df.columns,
            y=transition_df.index,
            color_continuous_scale='Blues',
            aspect="auto"  # or "equal"
        )

        # Customize hover information to switch start and end zone positions
        fig.update_traces(
            hovertemplate='Start Zone: %{y}<br>End Zone: %{x}<br>Number of Touches: %{z}<extra></extra>'
        )

        # Update the layout for better visuals
        fig.update_layout(
            title=f"Touch Transition Heatmap {limbs[i]}",
            xaxis_title="End Zone",
            yaxis_title="Start Zone",
            coloraxis_colorbar=dict(title="Number of Touches")
        )

        fig.write_html(output_folder + f"/heatmap_{limbs[i]}.html")
        #fig.show()


    
    def plot_touch_visualization_all_4(data_dicts, image_paths):
        # Create subplots with 4 columns
        fig = make_subplots(rows=1, cols=4, subplot_titles=("Left Hand", "Right Hand", "Left Leg", "Right Leg"),
                            horizontal_spacing=0.02)  # Adjust spacing as needed

        # Iterate over each dictionary and image path to create individual plots
        for i in range(4):
            data_dict = data_dicts[i]
            image_path = image_paths[i]

            # Load the image to get its dimensions
            img = Image.open(image_path)
            img_width, img_height = img.size

            # Encode the image in base64
            with open(image_path, "rb") as image_file:
                encoded_image = base64.b64encode(image_file.read()).decode()

            # Initialize lists for storing touch data
            x_coords = []
            y_coords = []
            colors = []
            sizes = []
            texts = []  # To store the hover information
            
            ongoing_touch = False

            for entry in data_dict:
                x = entry.get('X')
                y = entry.get('Y')
                onset = entry.get('Onset')
                frame = entry.get('Frame')
                zone = entry.get('Zones')

                if pd.notna(x) and pd.notna(y):
                    # Handle multiple values by taking the first one
                    x = str(x)
                    y = str(y)
                    x = float(x.split(',')[0].strip())
                    y = float(y.split(',')[0].strip())

                    hover_text = f"Frame: {frame}<br>X: {x}<br>Y: {y}<br>Onset: {onset}<br>Zone: {zone}"
                    texts.append(hover_text)

                    if onset == 'On' and not ongoing_touch:
                        # Starting a new touch
                        x_coords = [x]
                        y_coords = [y]
                        colors = ['green']
                        sizes = [15]  # Larger size for better visibility
                        ongoing_touch = True
                    elif onset == 'On' and ongoing_touch:
                        # Continuing an ongoing touch
                        x_coords.append(x)
                        y_coords.append(y)
                        colors.append('black')
                        sizes.append(8)  # Smaller size for intermediate points
                    elif onset == 'Off' and ongoing_touch:
                        # Ending a touch
                        x_coords.append(x)
                        y_coords.append(y)
                        colors.append('red')
                        sizes.append(15)  # Larger size for better visibility
                        
                        # Add the scatter for this touch
                        scatter = go.Scatter(
                            x=x_coords,
                            y=y_coords,
                            mode='markers+lines',
                            marker=dict(color=colors, size=sizes),
                            line=dict(color='black', width=2, dash='dot'),
                            name=f'Touch Path {i+1}',
                            text=texts,  # Custom text for hover
                            hovertemplate='%{text}<extra></extra>'
                        )
                        fig.add_trace(scatter, row=1, col=i+1)
                        
                        ongoing_touch = False
                        texts = []  # Clear texts after ending a touch

            # Add the background image to each subplot
            fig.add_layout_image(
                dict(
                    source=f'data:image/png;base64,{encoded_image}',
                    xref="x",
                    yref="y",
                    x=0,
                    y=img_height,  # The y position is set to the height of the image for top-left origin
                    xanchor="left",
                    yanchor="bottom",  # This ensures that the y-axis starts from the top
                    sizex=img_width,  # Use image width for correct scaling
                    sizey=img_height,  # Use image height for correct scaling
                    sizing="stretch",
                    opacity=1,
                    layer="below"
                ),
                row=1,
                col=i+1
            )

            # Update layout for the specific subplot
            fig.update_xaxes(visible=False, range=[0, img_width], row=1, col=i+1)
            fig.update_yaxes(visible=False, range=[0, img_height], row=1, col=i+1, scaleanchor="x", scaleratio=1)

        # Set overall layout properties
        fig.update_layout(
            height=img_height + 100,  # Adjust the height slightly if needed
            width=img_width * 4,  # Adjust width to accommodate four subplots side by side
            showlegend=False,
            margin=dict(l=0, r=0, t=50, b=40),  # Increase bottom margin and reduce top margin to move plots higher
        )

        # Invert the y-axis to match image coordinate system (top-left origin)
        fig.update_yaxes(autorange="reversed")

        # Show the plot
        fig.write_html(output_folder + "/touch_trajectory.html")
        #fig.show()




    image_path = "icons/RH.png"  # Replace with your image path
    image_paths = [
        "icons/LH.png",  # Replace with your actual paths
        "icons/RH.png",
        "icons/LL.png",
        "icons/RL.png"
    ]


    def analyze_baby_touch_data_seconds(
        limbs, total_touches_list, touch_durations_list, total_duration_list, 
        percentage_touching_list, average_touch_duration_list, touch_rate_list, output_file_path,
        total_frames, frame_rate,stdev_list
    ):
        # Convert frame-based data to seconds-based data
        total_duration_list_seconds = [duration / frame_rate for duration in total_duration_list]
        touch_durations_list_seconds = [[duration / frame_rate for duration in durations] for durations in touch_durations_list]
        average_touch_duration_list_seconds = [duration / frame_rate for duration in average_touch_duration_list]
        stdev_list_seconds = [
    (duration / frame_rate) if duration is not None else None 
    for duration in stdev_list
]
        # Adjust touch rate to be per second instead of per 100 frames
        touch_rate_list_seconds = [rate *frame_rate for rate in touch_rate_list]
        
        # Recalculate percentage touching in seconds
        if total_frames != 0 and frame_rate !=0: percentage_touching_list_seconds = [(duration / (total_frames / frame_rate)) * 100 for duration in total_duration_list_seconds]

        # Create a DataFrame for seconds-based data
        data_seconds = {
            'Limb': limbs,
            'Total Touches': total_touches_list,
            'Touch Durations [Seconds]': touch_durations_list_seconds,
            'Total Duration [Seconds]': total_duration_list_seconds,
            'Average Touch Duration [Seconds]': average_touch_duration_list_seconds,
            'Percentage Touching [Seconds]': percentage_touching_list_seconds,
            'Touch Rate [Touches per 100 Seconds]': touch_rate_list_seconds,
            'Standart Deviation [Seconds]': stdev_list_seconds
        }

        df_seconds = pd.DataFrame(data_seconds)

        # Calculate the combined averages and rates in seconds
        combined_total_touches = sum(total_touches_list)
        combined_total_duration_seconds = sum(total_duration_list_seconds)
        if total_frames != 0: combined_percentage_touching_seconds = (combined_total_duration_seconds / (total_frames / frame_rate)) * 100
        if combined_total_touches != 0: combined_average_touch_duration_seconds = sum(
            [sum(durations) for durations in touch_durations_list_seconds]
        ) / combined_total_touches
        if combined_total_duration_seconds != 0:combined_touch_rate_seconds = 100*(combined_total_touches /(total_frames/frame_rate))
        touch_durations_list_in_one = [item for sublist in touch_durations_list_seconds for item in sublist]
        if len(touch_durations_list_in_one)>=2:
            stdev_of_all = statistics.stdev(touch_durations_list_in_one)
        else:
            stdev_of_all = None
        # Append combined data to the DataFrame
        combined_data_seconds = {
            'Limb': 'Combined',
            'Total Touches': combined_total_touches,
            'Touch Durations [Seconds]': None,  # Combined data doesn't make sense for this field
            'Total Duration [Seconds]': combined_total_duration_seconds,
            'Average Touch Duration [Seconds]': combined_average_touch_duration_seconds,
            'Percentage Touching [Seconds]': combined_percentage_touching_seconds,
            'Touch Rate [Touches per 100 Seconds]': combined_touch_rate_seconds,
            'Standart Deviation [Seconds]': stdev_of_all
        }

        df_seconds = pd.concat([df_seconds, pd.DataFrame([combined_data_seconds])], ignore_index=True)

        # Save the DataFrame to a CSV file
        df_seconds.to_csv(output_file_path, index=False)

        return df_seconds



    def analyze_baby_touch_data(
        limbs, total_touches_list, touch_durations_list, total_duration_list, 
        percentage_touching_list, average_touch_duration_list, touch_rate_list, output_file_path,total_frames,stdev_list
    ):
        # Create a DataFrame
        #touch_rate_list = [element * 100 for element in touch_rate_list]
        data = {
            'Limb': limbs,
            'Total Touches': total_touches_list,
            'Touch Durations [Frames]': touch_durations_list,
            'Total Duration [Frames]': total_duration_list,
            'Average Touch Duration [Frames]': average_touch_duration_list,
            'Percentage Touching': percentage_touching_list,
            'Touch Rate [Touch per 100 Frames]': touch_rate_list,
            'Standart Deviation [Frames]': stdev_list
        }

        df = pd.DataFrame(data)
        #percentage_touching = (total_duration / total_frames) * 100
        # Calculate the combined averages and rates
        combined_total_touches = sum(total_touches_list)
        combined_total_duration = sum(total_duration_list)
        if total_frames != 0:combined_percentage_touching = (combined_total_duration / total_frames) * 100
        combined_average_touch_duration = sum([sum(durations) for durations in touch_durations_list]) / combined_total_touches
        if total_frames != 0:combined_touch_rate = (combined_total_touches / total_frames)* 100
        touch_durations_list_in_one = [item for sublist in touch_durations_list for item in sublist]
        if len(touch_durations_list_in_one)>=2:
            stdev_of_all = statistics.stdev(touch_durations_list_in_one)
        else:
            stdev_of_all = None
        # Append combined data to the DataFrame
        combined_data = {
            'Limb': 'Combined',
            'Total Touches': combined_total_touches,
            'Touch Durations [Frames]': None,  # Combined data doesn't make sense for this field
            'Total Duration [Frames]': combined_total_duration,
            'Average Touch Duration [Frames]': combined_average_touch_duration,
            'Percentage Touching': combined_percentage_touching,
            'Touch Rate [Touch per 100 Frames]': combined_touch_rate,
            'Standart Deviation [Frames]': stdev_of_all
        }

        df = pd.concat([df, pd.DataFrame([combined_data])], ignore_index=True)

        # Save the DataFrame to a CSV file
        df.to_csv(output_file_path, index=False)

        return df
    if debug:
        print("total_touches_list:", total_touches_list)
        print("touch_durations_list:", touch_durations_list)
        print("total_duration_list:", total_duration_list)
        print("percentage_touching_list:", percentage_touching_list)
        print("average_touch_duration_list:", average_touch_duration_list)
        print("touch_rate_list:", touch_rate_list)
        print("onset_count_distribution_list:", onset_count_distribution_list)
        print("zone_touch_count_list:", zone_touch_count_list)
        #print("Standart Deviation [Frames]:", statistics.stdev(touch_durations_list))
    output_file_path = output_folder + '/analysis_table_frames.csv'

    # Call the function with your variables
    result_df = analyze_baby_touch_data(
        limbs=limbs,
        total_touches_list=total_touches_list,
        touch_durations_list=touch_durations_list,
        total_duration_list=total_duration_list,
        percentage_touching_list=percentage_touching_list,
        average_touch_duration_list=average_touch_duration_list,
        touch_rate_list=touch_rate_list,
        output_file_path=output_file_path,
        total_frames=total_frames,
        stdev_list = stdev_list
    )
    frame_rate = 30
    output_file_path = output_folder + '/analysis_table_seconds.csv'
    result_df = analyze_baby_touch_data_seconds(
        limbs=limbs,
        total_touches_list=total_touches_list,
        touch_durations_list=touch_durations_list,
        total_duration_list=total_duration_list,
        percentage_touching_list=percentage_touching_list,
        average_touch_duration_list=average_touch_duration_list,
        touch_rate_list=touch_rate_list,
        output_file_path=output_file_path,
        total_frames=total_frames,
        frame_rate=frame_rate,
        stdev_list = stdev_list
    )
    #can i put the data under some subplot?
    # Now result_df contains the DataFrame with your results, and the data is saved to 'baby_touch_analysis_function.csv'
    if debug:print(result_df)

    

    plot_touch_visualization_all_4(data_dicts, image_paths)

    

    data = result_df

    # Define the columns you want to show and the order in which you want them
    columns_to_display = ['Limb','Total Touches','Total Duration [Seconds]','Average Touch Duration [Seconds]','Standart Deviation [Seconds]','Percentage Touching [Seconds]','Touch Rate [Touches per 100 Seconds]']  # Replace with actual column names and order

    # Format data, excluding the columns that are not in 'columns_to_display'
    formatted_data = {
    k: [f"{int(v)}" if k == 'Total Touches' and isinstance(v, (int, float)) else f"{v:.2f}" if isinstance(v, (int, float)) else v 
        for v in data[k]] 
    for k in columns_to_display
}

    # Create the table
    fig = go.Figure(data=[go.Table(
        header=dict(values=list(formatted_data.keys()),
                    fill_color='paleturquoise',
                    align='left'),
        cells=dict(values=[formatted_data[k] for k in formatted_data.keys()],
                fill_color='lavender',
                align='left'))
    ])

    # Update layout for better presentation
    fig.update_layout(
        title=f"Touch Analysis Data (Lenght of video: {total_frames/frame_rate} Seconds)",
        title_x=0.5,  # Center the title
        margin=dict(l=10, r=10, t=50, b=10),  # Adjust margins
        width=800,
        height=400
    )

    # Show the table
    fig.write_html(output_folder + f"/table.html")
    #fig.show()

    if debug:print("Touch sequence list:",    onset_count_distribution_list)
    touch_sequence_list=onset_count_distribution_list
    all_keys = set()
    for d in touch_sequence_list:
        all_keys.update(d.keys())
    all_keys = sorted(all_keys)

    # Prepare data for plotting
    sums_per_key = {key: [d[key] for d in touch_sequence_list] for key in all_keys}

    # Create the plotly bar chart
    fig = go.Figure()

    for idx, d in enumerate(touch_sequence_list):
        fig.add_trace(go.Bar(
            x=list(all_keys),
            y=[d.get(key, 0) for key in all_keys],
            name=limbs[idx],
            hovertext=[f'{limbs[idx]}<br>Lenght: {key}<br>Number of touches: {d.get(key, 0)}' for key in all_keys],
            hoverinfo='text',  # Use 'text' to display hovertext
        ))

    # Update layout
    fig.update_layout(
        barmode='stack',
        title='Touch lenght distribution',
        xaxis_title='Lenght of touch [number of zones]',
        yaxis_title='Number of touches',
    )

    # Show the plot
    fig.write_html(output_folder + f"/histogram.html")
    #fig.show()
    
    def create_touch_duration_histogram(touch_durations_list, frame_rate, limbs, output_folder):
        # Step 1: Convert frame counts to seconds and round to the nearest integer
        touch_sequence_list = []
        for touch_durations in touch_durations_list:
            # Convert durations to seconds and round them to integers
            duration_in_seconds = [math.ceil(touch_duration / frame_rate) for touch_duration in touch_durations]
            # Count the occurrences of each duration
            onset_count_distribution = dict(Counter(duration_in_seconds))
            touch_sequence_list.append(onset_count_distribution)
        
        # Step 2: Prepare the keys (unique touch durations in seconds)
        all_keys = set()
        for d in touch_sequence_list:
            all_keys.update(d.keys())
        all_keys = sorted(all_keys)

        # Step 3: Prepare data for plotting
        fig = go.Figure()

        for idx, d in enumerate(touch_sequence_list):
            fig.add_trace(go.Bar(
                x=list(all_keys),
                y=[d.get(key, 0) for key in all_keys],
                name=limbs[idx],
                hovertext=[f'{limbs[idx]}<br>Length: {key} sec<br>Number of touches: {d.get(key, 0)}' for key in all_keys],
                hoverinfo='text',  # Use 'text' to display hovertext
            ))

        # Step 4: Update layout
        fig.update_layout(
            barmode='stack',
            title='Touch Duration Distribution',
            xaxis_title='Touch Duration [second])',
            yaxis_title='Number of Touches',
            xaxis=dict(type='category'),
        )

        # Step 5: Save the plot as an HTML file
        fig.write_html(output_folder + "/histogram_2.html")

    create_touch_duration_histogram(touch_durations_list, frame_rate, limbs, output_folder)
    
    #name = "cat_mp4"
    folder_path = output_folder + "/"
    graphs = ["touch_trajectory.html","table.html","histogram.html","histogram_2.html","heatmap_LH.html", "heatmap_RH.html", "heatmap_LL.html", "heatmap_RL.html"]

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{name}</title>
    </head>
    <body>
        <h1>{name}</h1>
    """

    for i, graph in enumerate(graphs, start=1):
        height = 800 if i == 1 else 400  # Double the height for the first graph
        html_content += f"""
        
        <h2>{graphs[i-1]}</h2>
        <iframe src="{graph}" width="100%" height="{height}"></iframe>
        """

    html_content += """
    </body>
    </html>
    """
    

    # Define the folder where you want to save the master HTML file
    output_folder = output_folder  # Replace with your desired folder path

    # Ensure the folder exists
    os.makedirs(output_folder, exist_ok=True)

    # Define the file name

    file_path = os.path.join(output_folder, f"master_{name}.html")

    # Save the combined HTML file into the specified folder
    with open(file_path, "w") as f:
        f.write(html_content)

    
    webbrowser.open(file_path)
if __name__ == "__main__":
    data_path ="Labeled_data/test/data/"
    output_folder = "Labeled_data/test/plots/"
    name = "test"
    debug = False
    do_analysis(data_path,output_folder,name,debug)