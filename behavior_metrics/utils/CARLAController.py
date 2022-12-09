#!/usr/bin/env python

"""This module contains the controller of the application.

This application is based on a type of software architecture called Model View Controller. This is the controlling part
of this architecture (controller), which communicates the logical part (model) with the user interface (view).

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""

import shlex
import subprocess
import threading
import cv2
import rospy
import os
import time
import rosbag
import json
import math

from std_srvs.srv import Empty
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from datetime import datetime
from utils.logger import logger
from utils.constants import CIRCUITS_TIMEOUTS
from std_msgs.msg import String
from utils import CARLAmetrics
from carla_msgs.msg import CarlaLaneInvasionEvent
from carla_msgs.msg import CarlaCollisionEvent

__author__ = 'sergiopaniego'
__contributors__ = []
__license__ = 'GPLv3'


class CARLAController:
    """This class defines the controller of the architecture, responsible of the communication between the logic (model)
    and the user interface (view).

    Attributes:
        data {dict} -- Data to be sent to the view. The key is a frame_if of the view and the value is the data to be
        displayed. Depending on the type of data the frame handles (images, laser, etc)
        pose3D_data -- Pose data to be sent to the view
        recording {bool} -- Flag to determine if a rosbag is being recorded
    """

    def __init__(self):
        """ Constructor of the class. """
        pass
        self.__data_loc = threading.Lock()
        self.__pose_loc = threading.Lock()
        self.data = {}
        self.pose3D_data = None
        self.recording = False
        self.cvbridge = CvBridge()
        #self.collision_sub = rospy.Subscriber('/carla/ego_vehicle/collision', CarlaCollisionEvent, self.__collision_callback)
        #self.lane_invasion_sub = rospy.Subscriber('/carla/ego_vehicle/lane_invasion', CarlaLaneInvasionEvent, self.__lane_invasion_callback)


    def __collision_callback(self, data):
        intensity = math.sqrt(data.normal_impulse.x**2 + data.normal_impulse.y**2 + data.normal_impulse.z**2)
        logger.info('Collision with {} (impulse {})'.format(data.other_actor_id, intensity))

    def __lane_invasion_callback(self, data):
        text = []
        for marking in data.crossed_lane_markings:
            if marking is CarlaLaneInvasionEvent.LANE_MARKING_OTHER:
                text.append("Other")
            elif marking is CarlaLaneInvasionEvent.LANE_MARKING_BROKEN:
                text.append("Broken")
            elif marking is CarlaLaneInvasionEvent.LANE_MARKING_SOLID:
                text.append("Solid")
            else:
                text.append("Unknown ")
        logger.info('Crossed line %s' % ' and '.join(text))

    # GUI update
    def update_frame(self, frame_id, data):
        """Update the data to be retrieved by the view.

        This function is called by the logic to update the data obtained by the robot to a specific frame in GUI.

        Arguments:
            frame_id {str} -- Identifier of the frame that will show the data
            data {dict} -- Data to be shown
        """
        try:
            with self.__data_loc:
                self.data[frame_id] = data
        except Exception as e:
            logger.info(e)

    def get_data(self, frame_id):
        """Function to collect data retrieved by the robot for an specific frame of the GUI

        This function is called by the view to get the last updated data to be shown in the GUI.

        Arguments:
            frame_id {str} -- Identifier of the frame.

        Returns:
            data -- Depending on the caller frame could be image data, laser data, etc.
        """
        try:
            with self.__data_loc:
                data = self.data.get(frame_id, None)
        except Exception:
            pass

        return data

    def update_pose3d(self, data):
        """Update the pose3D data retrieved from the robot

        Arguments:
            data {pose3d} -- 3D position of the robot in the environment
        """
        try:
            with self.__pose_loc:
                self.pose3D_data = data
        except Exception:
            pass

    def get_pose3D(self):
        """Function to collect the pose3D data updated in `update_pose3d` function.

        This method is called from the view to collect the pose data and display it in GUI.

        Returns:
            pose3d -- 3D position of the robot in the environment
        """
        return self.pose3D_data

    # Simulation and dataset
    def reset_carla_simulation(self):
        logger.info("Restarting simulation")

    def pause_carla_simulation(self):
        logger.info("Pausing simulation")
        self.pilot.stop_event.set()

    def unpause_carla_simulation(self):
        logger.info("Resuming simulation")
        self.pilot.stop_event.clear()

    def record_rosbag(self, topics, dataset_name):
        """Start the recording process of the dataset using rosbags

        Arguments:
            topics {list} -- List of topics to be recorde
            dataset_name {str} -- Path of the resulting bag file
        """

        if not self.recording:
            logger.info("Recording bag at: {}".format(dataset_name))
            self.recording = True
            command = "rosbag record -O " + dataset_name + " " + " ".join(topics) + " __name:=behav_bag"
            command = shlex.split(command)
            with open("logs/.roslaunch_stdout.log", "w") as out, open("logs/.roslaunch_stderr.log", "w") as err:
                self.rosbag_proc = subprocess.Popen(command, stdout=out, stderr=err)
        else:
            logger.info("Rosbag already recording")
            self.stop_record()

    def stop_record(self):
        """Stop the rosbag recording process."""
        if self.rosbag_proc and self.recording:
            logger.info("Stopping bag recording")
            self.recording = False
            command = "rosnode kill /behav_bag"
            command = shlex.split(command)
            with open("logs/.roslaunch_stdout.log", "w") as out, open("logs/.roslaunch_stderr.log", "w") as err:
                subprocess.Popen(command, stdout=out, stderr=err)
        else:
            logger.info("No bag recording")

    def reload_brain(self, brain, model=None):
        """Helper function to reload the current brain from the GUI.

        Arguments:
            brain {srt} -- Brain to be reloadaed.
        """
        logger.info("Reloading brain... {}".format(brain))

        self.pause_pilot()
        self.pilot.reload_brain(brain, model)

    # Helper functions (connection with logic)

    def set_pilot(self, pilot):
        self.pilot = pilot

    def stop_pilot(self):
        self.pilot.kill_event.set()

    def pause_pilot(self):
        self.pilot.stop_event.set()

    def resume_pilot(self):
        self.start_time = datetime.now()
        self.pilot.start_time = datetime.now()
        self.pilot.stop_event.clear()

    def initialize_robot(self):
        self.pause_pilot()
        self.pilot.initialize_robot()


    def record_metrics(self, metrics_record_dir_path, world_counter=None, brain_counter=None, repetition_counter=None):
        logger.info("Recording metrics bag at: {}".format(metrics_record_dir_path))
        self.start_time = datetime.now()        
        if world_counter is not None:
            current_world_head, current_world_tail = os.path.split(self.pilot.configuration.current_world[world_counter])
        else:
            current_world_head, current_world_tail = os.path.split(self.pilot.configuration.current_world)
        if brain_counter is not None:
            current_brain_head, current_brain_tail = os.path.split(self.pilot.configuration.brain_path[brain_counter])
        else:
            current_brain_head, current_brain_tail = os.path.split(self.pilot.configuration.brain_path)
        self.experiment_metadata = {
            'world': current_world_tail,
            'brain_path': current_brain_tail,
            'robot_type': self.pilot.configuration.robot_type
        }
        if hasattr(self.pilot.configuration, 'experiment_model'):
            if brain_counter is not None:
                self.experiment_metadata['experiment_model'] = self.pilot.configuration.experiment_model[brain_counter]
            else:
                self.experiment_metadata['experiment_model'] = self.pilot.configuration.experiment_model
        if hasattr(self.pilot.configuration, 'experiment_name'):
            self.experiment_metadata['experiment_name'] = self.pilot.configuration.experiment_name
            self.experiment_metadata['experiment_description'] = self.pilot.configuration.experiment_description
            if hasattr(self.pilot.configuration, 'experiment_timeouts'):
                self.experiment_metadata['experiment_timeout'] = self.pilot.configuration.experiment_timeouts[world_counter]
            else:
                self.experiment_metadata['experiment_timeout'] = CIRCUITS_TIMEOUTS[os.path.basename(self.experiment_metadata['world'])] * 1.1
            self.experiment_metadata['experiment_repetition'] = repetition_counter

        self.metrics_record_dir_path = metrics_record_dir_path
        time_str = time.strftime("%Y%m%d-%H%M%S")
        self.experiment_metrics_filename = time_str + '.bag'
        topics = ['/carla/ego_vehicle/odometry', '/carla/ego_vehicle/collision', '/carla/ego_vehicle/lane_invasion', '/clock']
        command = "rosbag record -O " + self.experiment_metrics_filename + " " + " ".join(topics) + " __name:=behav_metrics_bag"
        command = shlex.split(command)
        with open("logs/.roslaunch_stdout.log", "w") as out, open("logs/.roslaunch_stderr.log", "w") as err:
            self.proc = subprocess.Popen(command, stdout=out, stderr=err)

    def stop_recording_metrics(self):
        logger.info("Stopping metrics bag recording")
        end_time = time.time()

        command = "rosnode kill /behav_metrics_bag"
        command = shlex.split(command)
        with open("logs/.roslaunch_stdout.log", "w") as out, open("logs/.roslaunch_stderr.log", "w") as err:
            subprocess.Popen(command, stdout=out, stderr=err)

        # Wait for rosbag file to be closed. Otherwise it causes error
        while os.path.isfile(self.experiment_metrics_filename + '.active'):
            pass

        self.experiment_metrics = CARLAmetrics.get_metrics(self.experiment_metrics_filename)

        try:
            self.save_metrics()
        except rosbag.bag.ROSBagException:
            logger.info("Bag was empty, Try Again")

        logger.info("* Experiment total real time -> " + str(end_time - self.pilot.pilot_start_time))
        self.experiment_metrics['experiment_total_real_time'] = end_time - self.pilot.pilot_start_time
        
        time_str = time.strftime("%Y%m%d-%H%M%S")
        
        with open(time_str + '.json', 'w') as f:
            json.dump(self.experiment_metrics, f)
        logger.info("Metrics stored in JSON file")

        logger.info("Stopping metrics bag recording")


    def save_metrics(self):
        experiment_metadata_str = json.dumps(self.experiment_metadata)
        experiment_metrics_str = json.dumps(self.experiment_metrics)
        with rosbag.Bag(self.experiment_metrics_filename, 'a') as bag:
            experiment_metadata_msg = String(data=experiment_metadata_str)
            experiment_metrics_msg = String(data=experiment_metrics_str)
            bag.write('/metadata', experiment_metadata_msg, rospy.Time(bag.get_end_time()))
            bag.write('/experiment_metrics', experiment_metrics_msg, rospy.Time(bag.get_end_time()))
        bag.close()

