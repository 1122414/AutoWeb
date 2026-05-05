import numpy as np
import matplotlib.pyplot as plt

def traffic_to_image(burst_sizes, width=32, height=32):
    """
    将 Burst Size 序列转换为灰度图
    :param burst_sizes: List 或 Array, 原始 Burst 大小序列
    :param width: 图像宽度
    :param height: 图像高度
    :return: 2D numpy 矩阵
    """
    # 1. 截断或填充，确保长度匹配 H * W
    target_len = width * height
    if len(burst_sizes) > target_len:
        data = np.array(burst_sizes[:target_len])
    else:
        data = np.pad(burst_sizes, (0, target_len - len(burst_sizes)), 'constant')
    
    # 2. 归一化处理 (映射到 0-255)
    # 注意：为了突出纹理，建议对 Size 取对数或进行 Min-Max 归一化
    data = np.log1p(np.abs(data)) # 取对数缓解长尾分布
    if np.max(data) > 0:
        data = (data / np.max(data) * 255).astype(np.uint8)
    
    # 3. Reshape 成 2D 矩阵
    image_matrix = data.reshape((height, width))
    return image_matrix

# --- 模拟实验数据展示证据 ---
# 模拟非 Tor 流量 (通常比较杂乱)
non_tor_data = np.random.poisson(lam=500, size=1024) 
# 模拟 Tor 流量 (由于 Cell Padding 和流控，会有重复的块状或条纹特征)
tor_data = np.tile([512, 0, 1024, 0, 512, 512], 200)[:1024] 

# 转换并绘图
img_non_tor = traffic_to_image(non_tor_data)
img_tor = traffic_to_image(tor_data)

fig, axes = plt.subplots(1, 2, figsize=(10, 5))
axes[0].imshow(img_non_tor, cmap='gray')
axes[0].set_title("Non-Tor Traffic (Visual Noise)")
axes[1].imshow(img_tor, cmap='gray')
axes[1].set_title("Tor Traffic (Visual Patterns/Textures)")
plt.show()