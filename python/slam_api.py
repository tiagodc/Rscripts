import os, rosbag, re, math, numpy, shutil, tf, sys, time
from sensor_msgs.msg import Imu
import sensor_msgs.point_cloud2 as pc2

reload(sys)  
sys.setdefaultencoding('utf8')

swap = 'xyz'

tops = [r'/velodyne_points', r'/ekf_quat', r'/imu_data']
recTopics = [r'/imu/data', r'/laser_odom_to_init', r'/velodyne_cloud_registered']
sourcePath = r'/home/tiago/catkin_aloam'

pcd2laz = '/home/tiago/pcd2laz/bin/Release/pcd2laz' 
pcdDir = 'pcd_temp'

rosKill = r'rosnode kill -a && killall -9 rosmaster'

def convertImu(sbgImu, sbgQuat):
    imuMsg = Imu()

    imuMsg.orientation.x = sbgQuat.quaternion.x
    imuMsg.orientation.y = sbgQuat.quaternion.y
    imuMsg.orientation.z = sbgQuat.quaternion.z
    imuMsg.orientation.w = sbgQuat.quaternion.w

    imuMsg.angular_velocity.x = sbgImu.gyro.x
    imuMsg.angular_velocity.y = sbgImu.gyro.y
    imuMsg.angular_velocity.z = sbgImu.gyro.z

    imuMsg.linear_acceleration.x = sbgImu.accel.x
    imuMsg.linear_acceleration.y = sbgImu.accel.y
    imuMsg.linear_acceleration.z = sbgImu.accel.z

    imuMsg.header = sbgImu.header
    imuMsg.header.frame_id = "imu_link"
    return imuMsg

def filterPointCloud2(msg, radius = None, swap = 'xyz'):

    swap = swap.lower()
    order = {'x':0, 'y':1, 'z':2}
    
    for i in range(len(swap)):
        ival = swap[i]
        order[ival] = i

    outData = []
    for p in pc2.read_points(msg, skip_nans=True):

        x = p[0]
        y = p[1]
        z = p[2]

        p = list(p)

        p[ order['x'] ] = x
        p[ order['y'] ] = y
        p[ order['z'] ] = z
        
        p = tuple(p)
        
        dst = math.sqrt( x**2 + y**2 + z**2 )

        if(radius is not None and dst > radius):
            continue
        
        outData.append(p)
    
    msg.header.frame_id = "laser_link"
    cld = pc2.create_cloud(msg.header, msg.fields, outData)

    return cld

def runSLAM(rBag, oLaz, oTxt, radius = 30, playRatio=0.25):

    global swap
    global sourcePath
    global tops
    global recTopics
    global pcd2laz
    global pcdDir
    global rosKill

    print('### processing: ' + rBag + ' @ radius ' + str(radius))

    wBag = re.sub(r'\.bag$', '_sensorMsg.bag', rBag)    
    bag = rosbag.Bag(rBag)
    writeBag = rosbag.Bag(wBag, 'w')

    imu = None
    for topic, msg, t in bag.read_messages(topics=tops):
        if( topic == tops[2] ):
            imu = msg

        if(topic == tops[1] and imu is not None):
            if(imu.time_stamp == msg.time_stamp):
                imuMsg = convertImu(imu, msg)
                writeBag.write('/imu/data', imuMsg, t)
            
        if(topic == tops[0]):
            writeBag.write(topic, filterPointCloud2(msg, radius, swap), t)

    bag.close()
    writeBag.close()

    oBag = re.sub(r'(\.bag$)', r'_slam.bag', wBag)
    bag = rosbag.Bag(wBag)
    
    loadTime = 10
    bagTime = math.ceil(loadTime + (bag.get_end_time() - bag.get_start_time()) / playRatio)
    bag.close()

    cmdStart = r'xterm -e "source ' + sourcePath + r'/devel/setup.bash && '
    cmdImu = r' --topics /velodyne_points'

    roslaunch = cmdStart + r' roslaunch aloam_velodyne aloam_velodyne_VLP_16.launch" &'
    os.system(roslaunch)

    time.sleep(loadTime)

    bagRecord = cmdStart + r'rosbag record ' + r' '.join(recTopics) + r' -O ' + oBag + r'" &'
    os.system(bagRecord)

    bagPlay = cmdStart + r'rosbag play ' + wBag + r' -r ' + str(playRatio) + r' ' + cmdImu + r'" &'
    os.system(bagPlay)

    time.sleep(bagTime+2)
    os.system(rosKill)

    if os.path.exists(pcdDir): 
        shutil.rmtree(pcdDir)

    os.makedirs(pcdDir)

    ### call ROS processes for exporting pcds from a bag file
    os.system('roscore &')

    time.sleep(2)

    pclCmd = 'rosrun pcl_ros bag_to_pcd ' + oBag + ' /velodyne_cloud_registered ' + pcdDir + ' /camera_init'

    os.system(pclCmd)
    os.system(rosKill)

    lazCmd = pcd2laz + ' -f ' + pcdDir + ' -o ' + oLaz
    os.system(lazCmd)

    shutil.rmtree(pcdDir)

    bag = rosbag.Bag(oBag)

    rad2deg = 180/math.pi
    angs = []
    slamPath = []
    for topic, msg, t in bag.read_messages(topics=recTopics):

        timeTag = float(msg.header.stamp.secs) + float(msg.header.stamp.nsecs) / 10**9

        if topic == recTopics[0]:
            quat = (
                msg.orientation.x,
                msg.orientation.y,
                msg.orientation.z,
                msg.orientation.w
            )

            euler = tf.transformations.euler_from_quaternion(quat)

            # time, roll, pitch, yaw
            info = [timeTag, euler[0] * rad2deg, euler[1] * rad2deg, euler[2] * rad2deg]
            angs.append(info)

        if topic == recTopics[1]:
            pose = msg.pose.pose

            quat = (
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w
            )

            euler = tf.transformations.euler_from_quaternion(quat)
            
            # time, x, y, z, roll, pitch, yaw
            info = [timeTag, pose.position.x, pose.position.y, pose.position.z, euler[0] * rad2deg, euler[1] * rad2deg, euler[2] * rad2deg]
            slamPath.append(info)

    bag.close()

    ### write the text files
    # if len(angs) > 0:
    #     angs = numpy.array(angs)
    #     oTxt = re.sub(r'\.bag$', r'_imu.txt', rBag)
    #     numpy.savetxt(oTxt, angs, fmt="%f")

    if len(slamPath) > 0:
        slamPath = numpy.array(slamPath)
        # oTxt = re.sub(r'\.bag$', r'_slam_path.txt', rBag)
        numpy.savetxt(oTxt, slamPath, fmt="%f")

    return (rBag, wBag, oBag)