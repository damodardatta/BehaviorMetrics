#!/usr/bin/env python
""" This module is responsible for handling the logic of the robot and its current brain.

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

import threading
import time
import rospy

from datetime import datetime
from brains.brains_handler import Brains
from robot.actuators import Actuators
from robot.sensors import Sensors
from utils.logger import logger
from utils.constants import MIN_EXPERIMENT_PERCENTAGE_COMPLETED

import numpy as np

__author__ = 'fqez'
__contributors__ = []
__license__ = 'GPLv3'

TIME_CYCLE = 50

from rosgraph_msgs.msg import Clock

clock_time = None


def clock_callback(clock_data):
    global clock_time
    clock_time = clock_data.clock.to_sec()


class Pilot(threading.Thread):
    """This class handles the robot and its brain.

    This class called Pilot that handles the initialization of the robot sensors and actuators and the
    brain that will control the robot. The main logic consists of an infinite loop called every 60 milliseconds that
    invoke an action from the brain.

    Attributes:
        controller {utils.controller.Controller} -- Controller instance of the MVC of the application
        configuration {utils.configuration.Config} -- Configuration instance of the application
        sensors {robot.sensors.Sensors} -- Sensors instance of the robot
        actuators {robot.actuators.Actuators} -- Actuators instance of the robot
        brains {brains.brains_handler.Brains} -- Brains controller instance
    """

    def __init__(self, configuration, controller, brain_path):
        """Constructor of the pilot class

        Arguments:
            configuration {utils.configuration.Config} -- Configuration instance of the application
            controller {utils.controller.Controller} -- Controller instance of the MVC of the application
        """

        self.controller = controller
        self.controller.set_pilot(self)
        self.configuration = configuration
        self.stop_event = threading.Event()
        self.kill_event = threading.Event()
        threading.Thread.__init__(self, args=self.stop_event)
        self.brain_path = brain_path
        self.robot_type = self.brain_path.split("/")[-2]
        self.sensors = None
        self.actuators = None
        self.brains = None
        self.initialize_robot()
        if self.robot_type == 'drone':
            self.pose3d = self.brains.active_brain.getPose3d()
            self.start_pose = np.array([self.pose3d[0], self.pose3d[1]])
        else:
            self.pose3d = self.sensors.get_pose3d('pose3d_0')
            self.start_pose = np.array([self.pose3d.getPose3d().x, self.pose3d.getPose3d().y])
        self.previous = datetime.now()
        self.checkpoints = []
        self.metrics = {}
        self.checkpoint_save = False
        self.max_distance = 0.5
        self.execution_completed = False

    def __wait_gazebo(self):
        """Wait for gazebo to be initialized"""

        # gazebo_ready = False
        self.stop_event.set()

    #         while not gazebo_ready:
    #             try:
    #                 self.controller.pause_gazebo_simulation()
    #                 gazebo_ready = True
    #                 self.stop_event.clear()
    #             except Exception as ex:
    #                 print(ex)

    def initialize_robot(self):
        """Initialize robot interfaces (sensors and actuators) and its brain from configuration"""
        self.stop_interfaces()
        if self.robot_type != 'drone':
            self.actuators = Actuators(self.configuration.actuators)
            self.sensors = Sensors(self.configuration.sensors)
        if hasattr(self.configuration, 'experiment_model') and type(self.configuration.experiment_model) != list:
            self.brains = Brains(self.sensors, self.actuators, self.brain_path, self.controller,
                                 self.configuration.experiment_model, self.configuration.brain_kwargs)
        else:
            self.brains = Brains(self.sensors, self.actuators, self.brain_path, self.controller,
                                 config=self.configuration.brain_kwargs)
        self.__wait_gazebo()

    def stop_interfaces(self):
        """Function that kill the current interfaces of the robot. For reloading purposes."""
        if self.sensors:
            self.sensors.kill()
        if self.actuators:
            self.actuators.kill()
        pass

    def run(self):
        """Main loop of the class. Calls a brain action every TIME_CYCLE"""
        "TODO: cleanup measure of ips"
        global clock_time
        clock_subscriber = rospy.Subscriber("/clock", Clock, clock_callback)
        it = 0
        ss = time.time()
        stopped_brain_metrics = False
        successful_iteration = False
        brain_iterations_time = []
        ros_iterations_time = []
        while not self.kill_event.is_set():
            start_time = datetime.now()
            start_time_ros = clock_time
            if not self.stop_event.is_set():
                self.execution_completed = False
                stopped_brain_metrics = True
                try:
                    self.brains.active_brain.execute()
                    successful_iteration = True
                except AttributeError as e:
                    logger.warning('No Brain selected')
                    logger.error(e)
                    successful_iteration = False
            else:
                if stopped_brain_metrics:
                    self.execution_completed = False
                    stopped_brain_metrics = False
                    successful_iteration = False
                    try:
                        self.brains.active_brain.inference_times = self.brains.active_brain.inference_times[10:-10]
                        mean_inference_time = sum(self.brains.active_brain.inference_times) / len(
                            self.brains.active_brain.inference_times)
                        frame_rate = len(self.brains.active_brain.inference_times) / sum(
                            self.brains.active_brain.inference_times)
                        gpu_inference = self.brains.active_brain.gpu_inference
                        first_image = self.brains.active_brain.first_image
                        logger.info('* Mean network inference time ---> ' + str(mean_inference_time) + ' s')
                        logger.info('* Frame rate ---> ' + str(frame_rate) + ' fps')
                    except Exception as e:
                        logger.error(e)
                        mean_inference_time = 0
                        frame_rate = 0
                        gpu_inference = False
                        first_image = None
                        logger.info('No inference brain')

                    mean_iteration_time = sum(brain_iterations_time) / len(brain_iterations_time)
                    mean_ros_iteration_time = sum(ros_iterations_time) / len(ros_iterations_time)
                    logger.info('* Mean brain iteration time ---> ' + str(mean_iteration_time) + ' s')
                    logger.info('* Mean ROS iteration time ---> ' + str(mean_ros_iteration_time) + ' s')
                    logger.info(hasattr(self.controller, 'experiment_metrics_filename'))
                    if hasattr(self.controller, 'experiment_metrics_filename'):
                        try:
                            logger.info('Saving metrics to ROS bag')
                            self.controller.save_metrics(mean_iteration_time, mean_inference_time, frame_rate,
                                                         gpu_inference, first_image)
                        except Exception as e:
                            logger.info('Empty ROS bag')
                            logger.error(e)
                    brain_iterations_time = []
                    self.execution_completed = True
            dt = datetime.now() - start_time
            ms = (dt.days * 24 * 60 * 60 + dt.seconds) * 1000 + dt.microseconds / 1000.0
            elapsed = time.time() - ss
            if elapsed < 1:
                it += 1
            else:
                ss = time.time()
                it = 0

            if ms < TIME_CYCLE:
                time.sleep((TIME_CYCLE - ms) / 1000.0)
            dt = datetime.now() - start_time
            ms = (dt.days * 24 * 60 * 60 + dt.seconds) * 1000 + dt.microseconds / 1000.0
            if successful_iteration:
                ros_iterations_time.append(clock_time - start_time_ros)
                brain_iterations_time.append(ms / 1000)
        clock_subscriber.unregister()
        logger.info('Pilot: pilot killed.')

    def stop(self):
        """Pause the main loop"""

        self.stop_event.set()

    def play(self):
        """Resume the main loop."""

        if self.is_alive():
            self.stop_event.clear()
        else:
            self.start()

    def kill(self):
        """Destroy the main loop. For exiting"""

        self.actuators.kill()
        self.kill_event.set()

    def reload_brain(self, brain_path, model=None):
        """Reload a brain specified by brain_path

        This function is useful if one wants to change the environment of the robot (simulated world).

        Arguments:
            brain_path {str} -- Path to the brain module to load.
        """
        self.brains.load_brain(brain_path, model=model)

    def finish_line(self):
        pose = self.pose3d.getPose3d()
        current_point = np.array([pose.x, pose.y])

        dist = (self.start_pose - current_point) ** 2
        dist = np.sum(dist, axis=0)
        dist = np.sqrt(dist)
        if dist < self.max_distance:
            return True
        return False
