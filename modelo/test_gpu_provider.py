import unittest
from unittest.mock import patch

from infer_onnx import create_session


class ProviderTests(unittest.TestCase):
    @patch("infer_onnx.ort")
    def test_explicit_cuda_requires_available_provider(self, ort):
        ort.get_available_providers.return_value = ["CPUExecutionProvider"]
        with self.assertRaisesRegex(RuntimeError, "CUDA no disponible"):
            create_session("unused.onnx", "cuda")
        ort.InferenceSession.assert_not_called()

    @patch("infer_onnx.ort")
    def test_detects_runtime_silent_cpu_fallback(self, ort):
        ort.get_available_providers.return_value = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        ort.InferenceSession.return_value.get_providers.return_value = ["CPUExecutionProvider"]
        with self.assertRaisesRegex(RuntimeError, "CUDA no se activó"):
            create_session("unused.onnx", "cuda")

    @patch("infer_onnx.ort")
    def test_cuda_initialization_error_is_not_retried_in_cpu(self, ort):
        ort.get_available_providers.return_value = ["CUDAExecutionProvider"]
        ort.InferenceSession.side_effect = RuntimeError("DLL missing")
        with self.assertRaisesRegex(RuntimeError, "No se pudo iniciar CUDA"):
            create_session("unused.onnx", "cuda")
        self.assertEqual(ort.InferenceSession.call_count, 1)


if __name__ == "__main__":
    unittest.main()
