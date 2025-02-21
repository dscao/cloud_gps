# cloud_gps
GPS定位平台数据接入homeassistant，包含“途强在线“、“途强物联”、“优驾盒子联网版”、“高德机车版”、“中移行车卫士”、"哈啰智能芯"等, 后续可能会加入等更多设备支持。

# 注意：
近期发现2G设备网络很差，很多区域都没有信号了。现在全部重新购买4G或5G设备了。

## 安装方法：

hacs安装： https://github.com/dscao/cloud_gps 

手动安装： 将custom_components中的文件夹复制到ha中对应目录中

重启ha后，强制刷新浏览器一次，进入集成，搜索： cloud_gps或云平台GPS ，按提示配置即可。

## 说明
对于平台未提供地址信息的，可以使用api调用百度、高德或腾讯地图的接口来显示具体地址信息，可按喜好选用。

添加集成功后，第一步需要进入选项启用设备，才会出现实现。如果没有设备，说明账号中没有可支持的gps设备。

哈啰智能芯接入参数获取方法：https://github.com/louliangsheng/hellobike


中移行车卫士参数从小程序中抓包。

车辆状态属性或实体值为： 离线 -- [断电] -- 行驶 -- [钥匙开启]-- [震动]--停车，优先级依次降低。 [] 表示不支持的则不会出现。

![11](https://github.com/dscao/cloud_gps/assets/16587914/fb3d9a8b-b7f3-48ea-92be-a37c72b62c41)


![12](https://github.com/dscao/cloud_gps/assets/16587914/e9917c31-80d6-466c-9ad3-f234f939276a)


![13](https://github.com/dscao/cloud_gps/assets/16587914/adfec487-8eb7-48ba-b9e9-629cca131c3a)


![14](https://github.com/dscao/cloud_gps/assets/16587914/f58a39f1-e5a0-4be0-8f79-baa612761d53)


