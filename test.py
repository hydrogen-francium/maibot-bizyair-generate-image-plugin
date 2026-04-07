import asyncio

import httpx

url = "https://api.bizyair.cn/w/v1/webapp/task/openapi/create"
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-xxx",
}

data = {
      "web_app_id": 39429,
      "suppress_preview_output": False,
      "input_values": {
        "17:BizyAir_NanoBananaPro.prompt": "atri里面的亚托莉，动漫画风",
        "17:BizyAir_NanoBananaPro.aspect_ratio": "1:1",
        "17:BizyAir_NanoBananaPro.resolution": "4K"
      }
    }


async def main():
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(url, headers=headers, json=data)

        print("status_code:", response.status_code)
        print("生成结果:", response.text)

        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(_demo())
