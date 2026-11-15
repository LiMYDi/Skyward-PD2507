# Skyward Recovery for iQOO Neo8（PD2301 / V2301A）

vivo iQOO Neo8 的第三方 Recovery，基于 [Kirk Lin](https://github.com/kirklin) 的 [Skyward](https://github.com/kirklin/skyward) 移植适配。

Skyward 不是一个自制的 TWRP 内核，而是一个用 Rust 重写的独立 recovery：自己画界面、自己做输入与解密，
设备信息、密钥服务等全部从手机已安装系统的分区里读取，而不是硬编码在配置里。

> 本仓库只包含**源码与构建脚本**，不包含任何原厂分区镜像，刷入第三方rec前请做好备份

---

## 设备

| 项目 | 值 |
| --- | --- |
| 机型 | iQOO Neo8（V2301A） |
| 代号 | PD2301 |
| 平台 | SM8475，第一代骁龙 8+（Snapdragon 8+ Gen 1） |
| 屏幕 | 1260 × 2800，density 560 |
| 系统 | OriginOS 6 / Android 16 |
| 固件 | 16.3.17.0.W10 |
| 安全补丁 | 2026-08-01 |
| 分区 | A/B + 动态分区 |

## 功能状态

| 功能 | 状态 | 备注 |
| --- | --- | --- |
| 显示/触摸/亮度 | ✅ | --- |
| data 解密（FBE） | ✅ | 输锁屏密码解密，依赖 KeyMint + Gatekeeper |
| 安装 ZIP / 镜像 | ✅ | 含原厂完整包、boot/recovery 等分区镜像 |
| 备份 / 恢复 | ✅ | 支持 EFS 分区与 metadata |
| WLAN | ✅ | QCA6490，扫描 / 连接 / DHCP / 联网探测 |
| OTG U 盘 | ✅ | 自动挂载到 `/mnt/usb`（exfat / vfat / ntfs / ext4） |
| 震动反馈 | ✅ | 走 vivo 私有驱动的 sysfs 接口 |
| ADB / MTP | ✅ | 解密后 MTP 可访问内部存储 |
| fastbootd | ✅ | 电脑侧需能识别 `VID_18D1&PID_4EE0` 的 WinUSB 驱动 |
| 截图 | ✅ | 音量+电源，解密后写入 `DCIM/Screenshots` |

## 与上游的差异

本仓库基于 Kirk Lin 的 Skyward 某一版快照移植，之后上游与本仓库各自演进，
因此差异分两部分：**移植适配**（让 Skyward 在这台机器上能起来）与**后续修复**（解密/WLAN/OTG/震动等问题）。

### 一、移植适配

| 改动 | 文件 | 原因 |
| --- | --- | --- |
| binder 头标记探测：新增 `HEADER_SYSTEM` / `HEADER_RECOVERY` 与 `choose_header()` | `skyward-binder/src/parcel.rs`、`service.rs` | PD2301 的 recovery ramdisk 带的是**系统版 libbinder**，服务目录只认其中一种标记，猜错则所有服务调用失效 |
| `update_prefix()` | `skyward-install/src/package.rs` | vivo 的完整包把 `payload.bin` 放在 zip 的子目录里，原版只认根目录 |
| 触摸面板未就绪时重试 | `skyward/src/input.rs` | 触摸驱动起得慢，原版一次打开失败就放弃 |
| 亮度范围补 `qcom,mdss-dsi-bl-max-level` | `skyward/src/ui.rs` | vivo 面板只在设备树里给标准最大值，不补就读不到背光范围 |
| fastbootd 端口接管：`prepare_fastboot_function` / `ensure_fastboot_port` / 状态记录 | `skyward/src/ui.rs` | 原版依赖 init 的属性动作拉函数；本机 init 不总能及时建好 `ffs.fastboot` 与其链接 |
| 开机置 `recovery.service=1` | `skyward/src/main.rs` | 原厂 init 等这个属性才加载充电与 ADSP 模块，否则读不到电量 |
| 打包时让 adbd 以默认类启动 | `skyward-pack/src/main.rs` | 原厂 recovery 的 adbd 是 `disabled`，被替换掉的 recovery 程序不再拉起它 |
| `--libraries` 参数 | `skyward-pack/src/main.rs` | 把恢复环境缺的库放进 ramdisk 的 `system/lib64` |
| 去掉 `Phone.bundle` 与 APEX bundle 挂载 | `skyward-programs/src/phone.rs`、`secure.rs` 等 | 本机不做 APEX 挂载，直接用 `/system/lib64`、`/vendor/lib64`（odm 再加 `/mnt/system/...`） |
| 移除「系统更新 / Skyward 更新」入口 | `skyward-ui/src/pages.rs`、`search.rs`、`app/nav.rs` | 本机没有可用的更新接口，避免入口点了没反应 |
| 设备配置 | `devices/pd2301.toml` | 屏幕、触摸模块、WLAN、备份分区等按真机填写 |

### 二、后续修复

1. **账号表补全**（`skyward-sys/src/ueventd.rs`）
   vivo 的 `passwd/group` 里只有自家 `oem_29xx` 账号，标准 AOSP 账号全靠 bionic 内置表。
   上游表缺 `inet`(3003) 等，导致 `ssgtzd` 的 socket 无法创建、整条加密服务链中断。
   已补全为 bionic `android_ids` 的完整集合（33 → 55 条）。

2. **恢复环境缺失库**（`skyward-pack` 的 `--libraries`）
   - `android.hardware.security.{keymint,secureclock,sharedsecret}-V1-ndk_platform.so`
     正常系统由 VNDK APEX（`/apex/com.android.vndk.v32`）提供，recovery 不挂载 APEX；
     ramdisk 里原有的同名条目是指向 `/vendor/lib64` 的断链。
   - `libcurl.so`：vivo 把它放在 system 分区，而 `ssgtzd` 是 vendor 程序。
   同时修正了 `add_libraries` 不能替换符号链接条目的问题。

3. **WLAN vendor 服务声明**（`[wlan] services`）
   vivo 的 VINTF 只把 `ISupplicantVendor` 声明为 HIDL，但 QTI 的 wpa_supplicant 启动时按
   **AIDL** 注册；servicemanager 不认就会让它立刻退出（255）。现在随 `ISupplicant` 一并告知。

4. **OTG 主机模式**（`[usb] host_mode`）
   vivo 的 `vusb` 驱动默认只做设备端，不写 `host_mode=enabled` 就永远枚举不出 U 盘。

5. **震动**（`[haptics] timed`）
   这机器没有 force-feedback 输入设备，震动由 vivo 私有驱动控制；
   新增 sysfs 后端，写 `/sys/class/qcom-haptics/{duration,activate}`。

6. **作者信息**（`[device] authors`）
   显示在「设备信息 → Recovery 版本」下方。

## 目录结构

```
.
├── build.sh                 # 完整构建脚本
├── devices/pd2301.toml      # PD2301 设备配置
├── libs/                    # 恢复环境缺少的 4 个共享库
├── upstream/                # Skyward 源码（Rust workspace）
└── LICENSE                  # GPL-3.0
```

## 构建

依赖：

- Rust（版本由 `upstream/rust-toolchain.toml` 指定，当前 1.100.0）与 `aarch64-linux-android` target
- [`cargo-ndk`](https://github.com/bbqsrc/cargo-ndk)
- Android NDK（本项目使用 r29）

```bash
export ANDROID_NDK_HOME=/path/to/android-ndk-r29
cargo install cargo-ndk --locked
./build.sh
```

产物：`out/skyward-pd2301.img`（填满 recovery 分区，带 AVB footer）。

> 构建需要原厂 `recovery.img` 作为底包，按官方全量包自行提取后放到 `stock/recovery.img`。
> 该文件被 `.gitignore` 忽略，仓库不提供。

## 刷入

```bash
fastboot getvar current-slot        # 确认槽位，通常是 a
fastboot flash recovery_<slot> out/skyward-pd2301.img
fastboot reboot recovery
```

要求：Bootloader 已解锁。首次进入后按提示输入锁屏密码解密 data。

### 关于 fastbootd

Skyward 的 fastbootd 使用 AOSP 标准 `VID_18D1 & PID_4EE0`，接口类为
`Class_FF & SubClass_42 & Prot_03`。Windows 自带的 `winusb.inf` 只匹配 `Prot_01`（adb），
所以**电脑上会显示为未知设备、`fastboot devices` 看不到**。
装一个支持该接口的驱动即可（Google USB Driver、OPPO/vivo 售后驱动、Zadig 绑定 WinUSB 均可）。

## 已知限制

- 解密需要用户手动输入锁屏密码，不支持默认密码。
- 端口同一时间只能是「设备」或「主机」：开 OTG 读 U 盘时 adb 会断开，拔掉后恢复。
- 未测试加密状态下的侧载 OTA。
- **双系统不可用**：本机分区表没有 `init_boot_a/b`，而 Skyward 的第二系统依赖它作为启动载体，入口不会出现。
- **系统更新检查不可用**：本机未适配 vivo 的 OTA 接口（与上游的小米协议不同），相关入口已移除。需要更新系统时，请把官方全量包放到存储或 U 盘，用「安装」直接刷入。

## 开发人员名单

- 致谢：原作者 [Kirk Lin](https://github.com/kirklin) —— Skyward 本体
- 适配工作：**酷安 @TNTHe（GitHub [@Qingizi7](https://github.com/Qingizi7)）、@Moecho**

本仓库由 **@Qingizi7（酷安 @TNTHe）** 维护。

免费资源，禁止倒卖。如果你是买来的，那么就是被骗了。

## 许可证

[GPL-3.0-or-later](LICENSE)。

本仓库是 Skyward 的派生作品，依 GPLv3 继续以同一许可证分发；
分发二进制时请同时提供对应源码。
