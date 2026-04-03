import asyncio

from bizyair_mcp_client import BizyAirMcpClient


async def _demo():
    client = BizyAirMcpClient(
        bearer_token="sk-xxx",
    )

    result = await client.generate_image(
        prompt="一只可爱的蓝发、白耳朵、白尾巴猫娘，动漫风格",
        aspect_ratio="1:1",
        resolution="1K",
    )

    print("图片 URL:", result.image_url)

    image_bytes = await result.download_bytes()
    print("图片字节长度:", len(image_bytes))

    saved_path = await result.save_to_file("generated_image.png")
    print("图片已保存:", saved_path.resolve())


if __name__ == "__main__":
    asyncio.run(_demo())
