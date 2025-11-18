# Home Assistant ESP-HA 集成

[English](README.md) | 简体中文

ESP-HA 集成是一个用于 Home Assistant 的本地集成组件，让您能够在 Home Assistant 中控制和管理基于 Espressif 芯片（ESP32、ESP32-S3、ESP32-C 系列）的 IoT 智能设备。该集成基于纯本地通信协议，无需云端服务，保护您的隐私。

## 目录

- [功能特性](#功能特性)
- [工作原理](#工作原理)
- [支持的设备](#支持的设备)
- [准备设备固件](#准备设备固件)
- [安装集成](#安装集成)
- [添加设备](#添加设备)
- [常见问题](#常见问题)
- [文档](#文档)

## 功能特性

- **纯本地控制**：所有通信都在局域网内完成，无需云端服务
- **自动发现**：通过 mDNS/Zeroconf 自动发现局域网内的 Espressif 设备
- **实时推送**：设备状态变化通过 HTTP 长连接实时推送到 Home Assistant
- **安全通信**：支持 ESP Local Control Sec1 加密协议
- **多设备类型**：支持灯光、传感器、二进制传感器、数字控制等多种设备类型
- **离线检测**：智能检测设备在线/离线状态，自动重连
- **隐私保护**：数据不离开您的局域网

## 工作原理

### 设备发现

ESP-HA 使用 mDNS/Zeroconf 协议自动发现局域网内的设备：

- **设备广播**：ESP 设备通过 mDNS 广播 `_esp_local_ctrl._tcp.local.` 服务
- **自动扫描**：Home Assistant 的 Zeroconf 集成扫描局域网内的 mDNS 服务
- **信息获取**：自动获取设备的 IP 地址、端口和设备信息

### 通信协议

- **连接方式**：HTTP 长连接
- **数据传输**：ESP Local Control 协议
- **加密支持**：Sec1 安全协议（可选）
- **状态推送**：设备主动推送状态变化到 HA
- **控制命令**：HA 通过 HTTP 请求控制设备

### 工作流程

#### 1. 设备发现和添加

```
┌─────────────┐         ┌──────────────┐         ┌─────────────────┐
│  ESP 设备   │         │  mDNS 服务   │         │ Home Assistant  │
└──────┬──────┘         └──────┬───────┘         └────────┬────────┘
       │                       │                          │
       │ 广播 mDNS 服务        │                          │
       │ _esp_local_ctrl._tcp  │                          │
       ├──────────────────────>│                          │
       │                       │                          │
       │                       │      扫描 mDNS 设备      │
       │                       │<─────────────────────────┤
       │                       │                          │
       │                       │  返回设备信息(node_id、IP、port 等) │
       │                       ├─────────────────────────>│
       │                                                  │
       │         HTTP 连接请求                            │
       │<─────────────────────────────────────────────────┤
       │                                                  │
       │         返回设备信息和属性列表                   │
       ├─────────────────────────────────────────────────>│
       │                                                  │
       │                        创建实体(Light, Sensor等) │
       │                                                  │
       │                                    显示在 HA UI  │
```

#### 2. 设备主动上报状态

```
┌─────────────┐                              ┌─────────────────┐
│  ESP 设备   │                              │ Home Assistant  │
└──────┬──────┘                              └────────┬────────┘
       │                                              │
       │  传感器数据变化                              │
       │  (温度、湿度、按键等)                        │
       │                                              │
       │  HTTP 推送属性更新                           │
       ├─────────────────────────────────────────────>│
       │                                              │
       │                                 更新实体状态 │
       │                                 实时显示在UI │
```

#### 3. HA 控制设备

```
┌──────┐      ┌─────────────────┐            ┌─────────────┐
│ 用户 │      │ Home Assistant  │            │  ESP 设备   │
└───┬──┘      └────────┬────────┘            └──────┬──────┘
    │                  │                            │
    │ 控制灯光         │                            │
    │ (开/关/亮度/颜色)│                            │
    ├─────────────────>│                            │
    │                  │                            │
    │                  │  HTTP 请求设置属性         │
    │                  ├───────────────────────────>│
    │                  │                            │
    │                  │                执行控制命令 │
    │                  │                (开灯/调光) │
    │                  │                            │
    │                  │           返回执行结果     │
    │                  │<───────────────────────────┤
    │                  │                            │
    │                  │           推送新状态确认   │
    │                  │<───────────────────────────┤
    │                  │                            │
    │  UI 更新显示     │                            │
    │<─────────────────┤                            │
```

**流程说明**：

1. **设备发现**：ESP 设备通过 mDNS 广播 `_esp_local_ctrl._tcp.local.` 服务，Home Assistant 通过 Zeroconf 集成扫描发现设备并获取设备 IP 地址和端口号
2. **添加设备**：Home Assistant 通过 HTTP 协议连接设备，获取设备所有属性和参数定义，并根据设备能力自动创建对应的实体（Light、Sensor 等）
3. **状态上报**：ESP 设备通过 HTTP 长连接主动推送状态变化到 Home Assistant，实现实时状态同步
4. **设备控制**：用户在 Home Assistant UI 中操作实体，Home Assistant 通过 HTTP 请求设置设备属性，设备执行控制命令后上报新状态以确认操作结果

## 支持的设备

### 硬件平台

**支持的芯片/模组**：
- ESP32（所有型号）
- ESP32-S3
- ESP32-C 系列（ESP32-C3、ESP32-C6 等）

**固件要求**：
- 必须运行基于 **ESP RainMaker** 的固件
- 启用 ESP Local Control 协议
- 在局域网内广播 mDNS 服务

### 支持的实体类型

ESP-HA 会根据设备配置自动创建相应的 Home Assistant 实体：

- **Light**（灯光）
  - 基本控制：power（开关）
  - 亮度调节：brightness（0-100）
  - 颜色控制：hue（色调 0-360°）、saturation（饱和度 0-100）
  - 色温控制：color temperature（支持的设备）
  
- **Sensor**（传感器）
  - temperature、humidity 等只读传感器数据
  
- **Binary Sensor**（二进制传感器）
  - 状态检测：door（门）、motion（运动）、vibration（震动）、touch（触摸）、plug（通断）
  
- **Number**（数字/阈值）
  - 可调节的阈值参数
  
- **Battery Energy**（电池能量）
  - battery_level、voltage、temperature、charging_status、battery_health
  
- **IMU Gesture**（IMU 手势）
  - gesture_type、toss、flip、shake、rotation、push、circle、clap 等手势识别

- **Interactive Input**（交互输入）
  - input_type、input_events、input_value

- **Low Power Sleep**（低功耗睡眠）
  - sleep_state、wake_reason、wake_window_status

## 准备设备固件

在使用 ESP-HA 集成之前，需要为 ESP 设备烧录基于 ESP RainMaker 的固件。

### 开发环境搭建

#### 1. 获取 ESP-IDF

**ESP-IDF 版本要求**：推荐使用 v5.1.2 或更新版本。

```bash
# 下载 ESP-IDF
mkdir -p ~/esp
cd ~/esp
git clone -b v5.1.2 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh
```

**配置环境变量**：

安装完成后，工具尚未添加至 PATH 环境变量。请在需要使用 ESP-IDF 的终端窗口中运行以下命令：

```bash
. $HOME/esp/esp-idf/export.sh
```

**注意**：命令开始的 `.` 与路径之间有一个空格。

如需经常使用 ESP-IDF，可以创建一个别名。将以下命令添加到 shell 配置文件（`.bashrc`、`.zshrc` 等）中：

```bash
alias get_idf='. $HOME/esp/esp-idf/export.sh'
```

添加后，通过重启终端窗口或运行 `source ~/.bashrc`（根据实际使用的 shell 配置文件调整）来刷新配置。之后可在任何终端窗口中运行 `get_idf` 来激活 ESP-IDF 环境。

#### 2. 获取 ESP RainMaker

```bash
cd ~/esp
git clone --recursive https://github.com/espressif/esp-rainmaker.git
```

### 编译和烧录固件

以 ESP RainMaker 的 `led_light` 示例为例：

#### 1. 进入示例目录

```bash
cd ~/esp/esp-rainmaker/examples/led_light
```

#### 2. 设置目标芯片

根据实际使用的 ESP 芯片型号设置编译目标：

```bash
# 示例：ESP32-C3
idf.py set-target esp32c3

# 其他常用芯片：esp32、esp32s3、esp32c6 等
```

#### 3. 编译固件

```bash
idf.py build
```

#### 4. 烧录固件

将 ESP 设备连接到计算机，执行：

```bash
idf.py flash
```

#### 5. 查看串口日志

```bash
idf.py monitor
```

退出串口监视器：按 `Ctrl + ]`

### 配置设备 WiFi

#### 方式 1：硬编码 WiFi（开发测试）

直接在代码中配置 WiFi，修改 `main/app_wifi.c`：

```c
esp_err_t app_wifi_start()
{
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = "your_wifi_ssid",      // 修改为你的 WiFi 名称
            .password = "your_password",    // 添加 WiFi 密码
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    xEventGroupWaitBits(network_event_group, NETWORK_CONNECTED_EVENT, false, true, portMAX_DELAY);
    return ESP_OK;
}
```

#### 方式 2：使用 ESP RainMaker APP（推荐）

1. 在手机上安装 ESP RainMaker APP：
   - iOS: App Store 搜索 "ESP RainMaker"
   - Android: Google Play 搜索 "ESP RainMaker"

2. 设备烧录固件后会进入配网模式（LED 闪烁）

3. 打开 APP，选择 "Add Device"，按照提示配置 WiFi

4. 配置完成后，设备会连接到你的 WiFi 网络

5. 此时设备已经可以被 Home Assistant 的 ESP-HA 集成自动发现

## 安装集成

> **版本要求**：
> - Home Assistant Core ≥ 2024.1.0
> - Python ≥ 3.12

### 方法 1：开发环境（用于测试和开发）

适用于从源码运行 Home Assistant 的开发环境：

```bash
# 1. 从 GitHub 下载集成
git clone https://github.com/your-repo/esp-ha.git

# 2. 复制到 Home Assistant 的 config 目录
cd /path/to/ha-core
mkdir -p config/custom_components
cp -r /path/to/esp-ha-source config/custom_components/esp-ha

# 3. 启动 Home Assistant
hass -c config
```

### 方法 2：生产环境（Home Assistant OS/Supervised）

适用于在树莓派等设备上运行的 Home Assistant OS 或 Supervised：

1. 在 Home Assistant 中安装 [Samba Share](https://github.com/home-assistant/addons/tree/master/samba) 插件
2. 在 Windows/Mac 上挂载 Home Assistant 的 Samba 共享（网络位置：`\\homeassistant.local\config`）
3. 在 Samba 共享中创建 `custom_components` 文件夹（如果不存在）
4. 将 esp-ha 代码复制到 `custom_components` 目录
5. 在 Home Assistant 中重启：**设置** > **系统** > **重启 Home Assistant**

## 添加设备

ESP-HA 集成会自动发现局域网内的 Espressif 设备：

1. 进入 **设置** > **设备与服务**
2. 点击右下角的 **"+ 添加集成"** 按钮
3. 搜索 **"ESP_HA"**
4. 选择 ESP-HA 集成，系统会自动扫描局域网内的设备
5. 从发现的设备列表中选择要添加的设备
6. 如果设备启用了安全模式，系统会提示输入 PoP 凭据

添加成功后，设备的所有实体会自动出现在 Home Assistant 中。

## 常见问题

### 1. 设备无法被发现

请依次检查以下项目：

- 确认 ESP 设备和 Home Assistant 在同一局域网内
- 检查设备串口日志，确认 WiFi 已连接并获取 IP 地址
- 检查设备串口日志，确认 mDNS 服务已成功启动
- 确认 Home Assistant 的 Zeroconf 集成已启用
- 检查防火墙是否阻止 mDNS 协议（UDP 端口 5353）

**查看设备 IP 地址**：
```bash
idf.py monitor  # 查看设备串口日志
```

### 2. Home Assistant 重启后 UI 显示 "Connection Lost"

这是浏览器 WebSocket 重连机制的正常现象。

**快速恢复方法**：强制刷新浏览器
- Windows/Linux：`Ctrl + Shift + R`
- macOS：`Cmd + Shift + R`

**说明**：
- **非正常关闭**（断电/崩溃）：浏览器使用指数退避策略重连，延迟 30-60 秒属于正常现象
- **正常关闭**（优雅重启）：WebSocket 正常断开，重连通常在 5-10 秒内完成

**如果重启后 UI 恢复时间超过 1 分钟**，可能原因：
- 集成数量过多导致初始化时间较长
- 历史数据库体积过大
- 系统资源（CPU/内存）不足

### 3. 设备显示离线

ESP-HA 具有自动重连机制：

- 设备短暂离线：ESP-HA 自动后台重连，设备上线后自动恢复
- 设备长时间离线：实体状态显示为 `Unavailable`，设备上线后自动恢复为 `Available`

**无需手动操作**，重连过程完全自动化。如设备永久下线，可在 Home Assistant 的 **设置 > 设备与服务** 中移除该集成。

### 4. 如何查看调试日志

在 Home Assistant 配置文件 `configuration.yaml` 中添加以下配置：

```yaml
logger:
  default: info
  logs:
    custom_components.esp-ha: debug
```

重启 Home Assistant 后，调试日志将记录设备发现、属性更新、连接状态变化等详细信息。

## 文档

- **ESP RainMaker**：[https://rainmaker.espressif.com/](https://rainmaker.espressif.com/)
- **ESP Local Control**：[ESP-IDF 编程指南](https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32/api-reference/protocols/esp_local_ctrl.html)
- **Home Assistant 开发**：[https://developers.home-assistant.io/](https://developers.home-assistant.io/)

## 许可证

MIT License

## 目录结构

```
custom_components/esp-ha/
├── __init__.py              # 集成入口，处理平台加载和配置项管理
├── const.py                 # 常量定义（域名、配置键等）
├── config_flow.py           # 配置流程处理（设备添加、重新配置、PoP认证等）
├── manifest.json            # 集成元数据（域名、版本、依赖项、功能声明等）
├── strings.json             # 多语言字符串定义（配置流程文本、错误提示等）
├── panel.py                 # Home Assistant 前端面板注册
├── light.py                 # Light 平台实现（灯光控制）
├── sensor.py                # Sensor 平台实现（传感器数据）
├── binary_sensor.py         # Binary Sensor 平台实现（二值传感器）
├── number.py                # Number 平台实现（数值输入）
├── battery_energy.py        # Battery Energy 平台实现（电池能量监控）
├── imu_gesture.py           # IMU Gesture 平台实现（IMU 手势识别）
├── interactive_input.py     # Interactive Input 平台实现（交互式输入）
├── low_power_sleep.py       # Low Power Sleep 平台实现（低功耗睡眠）
├── gesture_images/          # 手势图片资源目录
└── esp_iot/                 # ESP Local Control 协议实现
    ├── __init__.py          # 模块初始化
    ├── esp_iot_api.py       # 核心 API 接口和设备连接管理
    ├── esp_iot_client.py    # HTTP 客户端实现
    ├── esp_iot_client_utils.py  # 客户端工具函数
    ├── esp_iot_discovery.py # 设备自动发现实现（属性解析）
    ├── esp_iot_local_ctrl.py    # ESP Local Control 协议客户端
    ├── esp_iot_network.py   # mDNS 网络监听和设备发现
    ├── esp_iot_parser.py    # 设备配置解析器
    ├── esp_iot_payload.py   # 协议负载处理和设备控制
    ├── esp_iot_spec.py      # 设备规格和能力定义
    ├── esp_iot_utils.py     # 通用工具函数和事件处理
    ├── esp_local_ctrl_lib/  # ESP Local Control 底层库
    │   ├── __init__.py             # 模块初始化
    │   ├── esp_local_ctrl.py       # ESP Local Control 协议实现
    │   ├── esp_local_ctrl_pb2.py   # Protocol Buffer 定义
    │   ├── proto_lc.py             # 本地控制协议定义
    │   ├── esp_prov/               # ESP Provisioning 库
    │   │   ├── esp_prov.py         # 设备配置帮助签实现
    │   │   ├── security/           # 安全协议（Sec0、Sec1、Sec2）
    │   │   ├── transport/          # 传输层（HTTP客户端）
    │   │   └── proto/              # 推送协议 Protocol Buffer 定义
    │   └── protocomm/              # 推送底层通讯协议 Protocol Buffer
    │       ├── constants_pb2.py    # 常量定义
    │       ├── session_pb2.py      # 会话管理
    │       ├── sec0_pb2.py         # 所有者授权（无密码）
    │       ├── sec1_pb2.py         # PoP 授权
    │       └── sec2_pb2.py         # 高级密码授权
