# PD2507 移植 Skyward 可行性评估（v2 · 已用原厂包取证）

- 评估日期：2026-11-18
- 目标机：V2507A / PD2507（iQOO Z10 Turbo+），OriginOS 6 / Android 16，`PD2507_A_16.1.11.1.W10.V000L1`
- 底包：vivo 官方全量包 `202603140311285424ef3f0a973f58505eeae6db72af3f.zip`（9.17 GB），`post-version` 与目标机**完全一致**
- 已解出：`images/boot.img`、`images/init_boot.img`、`images/vendor_boot.img`

## 一、取到的硬证据

从 OTA 的 `payload.bin` manifest 解析出 39 个分区，**没有任何 `recovery` 分区**：

```
apusys boot ccu connsys_gnss dpm dtbo gpueb gz init_boot ise lk logo
mcf_ota mcupm modem mvpu_algo odm odm_dlkm pi_img preloader_raw product
pvmfw rotfw scp spmfw sspm storage system system_dlkm system_ext tee
vbmeta vbmeta_system vbmeta_vendor vcp vendor vendor_boot
```

recovery 的真实位置：`vendor_boot`（134 MB，header v4）里的第二个 ramdisk 片段。

| 片段 | 类型 | 大小 | 名字 |
| --- | --- | --- | --- |
| 0 | 1 = PLATFORM | 47,334,739 | 空 |
| 1 | **2 = RECOVERY** | **58,228,830** | `recovery` |

平台信息：

- `vendor_boot` cmdline：`product.version=PD2507_A_16.1.11.1.W10` / **`product.solution=MTK`**
- `first_stage_ramdisk/fstab.emmc` 出自 `vendor/mediatek/proprietary/hardware/fstab/mt6991/` → **mt6991（天玑 9400+）**，`ro.hardware=emmc`
- A/B + 动态分区（system/vendor/product/odm 都是 `logical`），userdata 是 f2fs + `fileencryption=aes-256-xts`，keydirectory `/metadata/vold/metadata_encryption`

## 二、结论

**技术上成立，但落点和 PD2301 完全不是一回事。**

PD2301 是「有 recovery 分区、刷 `recovery_a`」；PD2507 是「没有 recovery 分区，recovery 是 vendor_boot 里的一个片段」。
所以：

- ❌ `fastboot flash recovery_a` 这条路不存在；
- ✅ 但 Skyward 的「替换原厂 recovery 程序、复用原厂服务」模型仍然成立——原厂 recovery 就是 AOSP recovery 套了 vivo 自己的 `system/bin/recovery`（4.6 MB）+ `recovery_resources/`；
- ⚠️ 工作量转移到了**打包**：`skyward-pack` 只会读 boot 镜像（header v3/v4）并换 ramdisk，现在需要新增「读 vendor_boot v4 头 + 片段表，只替换 type=2 的 recovery 片段，重排偏移与 AVB footer」这条路径。这是这次移植最大的一块新代码。

## 三、代码对照（哪些必须动）

| 位置 | 现状 | PD2507 上要变成 |
| --- | --- | --- |
| `skyward-block/src/fstab.rs:8` | `/vendor/etc/fstab.qcom`、`/odm/etc/fstab.qcom` | 这台是 `first_stage_ramdisk/fstab.emmc` / `fstab.emmc`。**必改**，否则块设备层起不来 |
| `skyward-bootimg/src/image.rs` | 只解析 boot `ANDROID!` v3/v4 | 新增 `VNDRBOOT` v4：解析片段表、按 type 定位 recovery 片段、重打包 |
| `skyward-pack` | `--stock recovery.img` | 新增 `--stock vendor_boot.img --fragment recovery` 模式 |
| `skyward/src/ui.rs:139` | 读 `/sys/module/msm_drm/...` 与 `qcom,mdss-dsi-bl-max-level` | MTK 是 `mtk_drm` 系，亮度上限要重写 |
| `devices/*.toml [haptics]` | `qcom-haptics/{duration,activate}` | 换 MTK vivo 的实际节点 |
| `devices/*.toml [wlan]` | QCA6490 / `ISupplicantVendor` | MTK connac 系，模块名与写入点全换 |
| `devices/*.toml [modules] load` | `vivo_ts` | 这台触控是 ST FTS（`/sys/devices/virtual/fts/*`）+ Novatek（`/proc/NVTSPI`），模块名要从 `vendor_dlkm` 查 |

## 四、原厂 recovery 里已经有的东西（好消息）

解出来的 58 MB ramdisk 有 864 个条目，关键部分：

- **解密链完整**：`system/bin/keystore2`、`libkeymint.so`、`libgatekeeper.so`、`android.hardware.security.keymint-V1~V4-ndk.so`、`weaver@1.0`、`plat_keystore2_key_contexts`
- **WLAN**：整套 `android.hardware.wifi*` 与 `supplicant` 的 HIDL/AIDL 库，还有 `init.recovery.wifi.rc`
- **触控**：`init.recovery.touch.rc` 明确给出节点——`fts_i2c`（ST）+ `NVTSPI`（Novatek）+ `gtp_tools`
- **服务框架**：`init.recovery.svc.rc` 起 `servicemanager` / `hwservicemanager` / `vndservicemanager` / `ashmemd` / `logd`
- 没有 `.ko`：模块在 `vendor_dlkm` 里，靠 recovery 的 init 加载——这和 Skyward「读原厂 init 学会启动方式」的设计正好对上

所以「能不能解密」这个原本最大的问号，基本可以划掉了：机制和 PD2301 同构，剩下的是试跑。

## 五、新的风险（比 PD2301 高）

1. **刷写目标变了**：改的是 `vendor_boot_a`，而正常开机也用它（platform 片段 47 MB）。虽然 bootloader 按 type 选片段、理论上只影响 recovery 模式，但**这是推测，必须实测**。
2. **AVB**：`vendor_boot` 在 vbmeta 覆盖范围内，改完要么关验证（`vbmeta` 刷 disable flag），要么接受 bootloader 拒绝。解锁 BL 通常能过，但 vivo 的 seccfg 状态要确认。
3. **没有电脑**：fastboot 需要主机，这台机器自己没法给自己 fastboot。**只能 root 下 dd 写分区，然后重启进 recovery**。所以第一步必须先验证 root 能不能写 `vendor_boot_a`。

## 六、编译

本机 aarch64，Google 的 NDK 没有 linux-aarch64 预编译工具链。三条路：

1. **GitHub Actions**（推荐）：x86_64 + 现成 NDK，改一份 workflow 就能出包，手机只负责下载和 dd。
2. 本机硬搭：apt 的 `clang`/`lld` + NDK 的 sysroot 绕过 cargo-ndk。要限 `-j2`（可用内存 3.8 GB）。
3. 借一台 x86_64 电脑。

## 七、推进顺序（修订）

0. 先确认 root 能读 `vendor_boot_a`（能不能写下一步再验）。
1. 从本机 dd 出 `vendor_boot_a`，和从全量包解出来的那份比对——**一致才说明底包对得上**。
2. 给 `skyward-bootimg` 加 vendor_boot 解析/重打包，先做「解出来再原样装回去」的往返测试（不改内容，验证字节级可逆）。
3. 再替换 recovery 片段里的 `system/bin/recovery`，打包。
4. dd 写 `vendor_boot_a` → 重启进 recovery，看能不能起、触摸准不准。
5. 起来之后再逐个打通：解密 → MTP/adb → fastbootd → WLAN → 震动。

## 八、当前进展（2026-11-18 晚）

- 已确认：容器层（vendor_boot 拆分/重组）**字节级可逆**，见 `vb/vboot.py` 与 `gha/tools/pd2507/build.py`，对原厂 134 MB 镜像跑过往返，sha256 不变。
- 已确认：RECOVERY 片段 ↔ 合成 boot 镜像 ↔ skyward-pack ↔ 回填，这条链的每一段都在本地验证过（除了 skyward-pack 本身，它要 Rust 才能跑）。
- 编译路线选定 **GitHub Actions**：`gha/` 下有工作流、打包脚本、设备配置，步骤见 `gha/PACKAGING.md`。
- 下一步：真机第一次起包，先只求「能进、能触摸、能关机」，亮度/震动/WLAN/触控参数第二轮补。
