from __future__ import annotations

import unittest
from unittest.mock import patch


class ImageResolutionTests(unittest.TestCase):
    def test_build_image_prompt_uses_fixed_resolution_presets(self) -> None:
        from services.protocol.conversation import build_image_prompt

        prompt = build_image_prompt("draw a product hero", "1536x864")

        self.assertIn("目标输出分辨率为 1536x864", prompt)
        self.assertIn("16:9 横版构图", prompt)
        self.assertNotIn("宽高比为 1536x864", prompt)

    def test_build_image_prompt_keeps_legacy_ratio_support(self) -> None:
        from services.protocol.conversation import build_image_prompt

        prompt = build_image_prompt("draw a poster", "9:16")

        self.assertIn("9:16 竖屏构图", prompt)

    def test_supported_image_size_metadata_maps_resolution_and_legacy_ratio(self) -> None:
        from services.protocol.conversation import image_size_metadata

        self.assertEqual(
            image_size_metadata("1536x864"),
            {"target_size": "1536x864", "target_width": 1536, "target_height": 864, "target_aspect_ratio": "16:9"},
        )
        self.assertEqual(
            image_size_metadata("16:9"),
            {"target_size": "16:9", "target_width": None, "target_height": None, "target_aspect_ratio": "16:9"},
        )
        for size, width, height, ratio in (
            ("1920x1080", 1920, 1080, "16:9"),
            ("1080x1920", 1080, 1920, "9:16"),
            ("2560x1440", 2560, 1440, "16:9"),
            ("1440x2560", 1440, 2560, "9:16"),
            ("3840x2160", 3840, 2160, "16:9"),
            ("2160x3840", 2160, 3840, "9:16"),
        ):
            with self.subTest(size=size):
                self.assertEqual(
                    image_size_metadata(size),
                    {
                        "target_size": size,
                        "target_width": width,
                        "target_height": height,
                        "target_aspect_ratio": ratio,
                    },
                )
        self.assertEqual(image_size_metadata(""), {})

    def test_common_resolution_presets_are_target_size_hints(self) -> None:
        from services.protocol.conversation import build_image_prompt, validate_image_size

        self.assertEqual(validate_image_size("1920 × 1080"), "1920x1080")
        prompt = build_image_prompt("draw a product hero", "3840x2160")

        self.assertIn("目标输出分辨率为 3840x2160", prompt)
        self.assertIn("4K UHD", prompt)
        self.assertNotIn("保证输出", prompt)

    def test_validate_image_size_rejects_unsupported_values(self) -> None:
        from services.protocol.conversation import validate_image_size

        with self.assertRaisesRegex(ValueError, "unsupported image size"):
            validate_image_size("123x456")

    def test_stream_image_outputs_rejects_unsupported_size_before_token_checkout(self) -> None:
        from services.protocol.conversation import ConversationRequest, ImageGenerationError, stream_image_outputs_with_pool

        request = ConversationRequest(model="gpt-image-2", prompt="draw", size="123x456")

        with patch("services.protocol.conversation.account_service.get_available_access_token") as get_token:
            with self.assertRaises(ImageGenerationError) as context:
                list(stream_image_outputs_with_pool(request))

        self.assertEqual(context.exception.status_code, 400)
        get_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
