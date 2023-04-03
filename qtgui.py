import os

os.environ['DISABLE_TELEMETRY'] = '1'
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] ='1'

import json
import random
import re
import sys
import traceback
import warnings

import numpy as np
import torch
from compel import Compel
from diffusers import (DDIMScheduler, DDPMScheduler, DEISMultistepScheduler,
                       DiffusionPipeline, EulerAncestralDiscreteScheduler,
                       EulerDiscreteScheduler, HeunDiscreteScheduler,
                       LMSDiscreteScheduler, PNDMScheduler, SchedulerMixin,
                       StableDiffusionImg2ImgPipeline, StableDiffusionPipeline,
                       UniPCMultistepScheduler)
from PIL import Image, PngImagePlugin
from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import (QAction, QFontMetrics, QIcon, QImage, QPalette,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup,
                               QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMenuBar, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QSplitter,
                               QStyle, QStyleOptionSlider, QTableWidget,
                               QTableWidgetItem, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

if sys.platform == 'darwin':
    from AppKit import NSURL, NSApplication, NSWorkspace
    from Foundation import NSBundle

warnings.filterwarnings('ignore')
from gfpgan import GFPGANer

warnings.resetwarnings()

# -------------------------------------------------------------------------------------------------
APP_NAME = 'SimpleDiffusion'
APP_VERSION = 0.1
IMAGES_PATH = 'images'
THUMBNAILS_PATH = 'thumbnails'

settings: QSettings = None
pipes: dict[str, DiffusionPipeline] = {}
schedulers: dict[str, SchedulerMixin] = {
    'ddim': DDIMScheduler,
    'ddpm': DDPMScheduler,
    'deis_multi': DEISMultistepScheduler,
    # 'dpm_multi': DPMSolverMultistepScheduler,
    # 'dpm': DPMSolverSinglestepScheduler,
    # 'k_dpm_2': KDPM2DiscreteScheduler,
    # 'k_dpm_2_a': KDPM2AncestralDiscreteScheduler,
    'k_euler': EulerDiscreteScheduler,
    'k_euler_a': EulerAncestralDiscreteScheduler,
    'k_heun': HeunDiscreteScheduler,
    'k_lms': LMSDiscreteScheduler,
    'pndm': PNDMScheduler,
    'uni_pc': UniPCMultistepScheduler,
}
gfpgan: GFPGANer = None

def set_default_setting(key: str, value):
    if not settings.contains(key):
        settings.setValue(key, value)

def bool_setting(key: str):
    value = settings.value(key)
    if value is not None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == 'true'
    return False

def pil_to_qimage(pil_image: Image.Image):
    data = pil_image.convert('RGBA').tobytes('raw', 'RGBA')
    qimage = QImage(data, pil_image.width, pil_image.height, QImage.Format_RGBA8888)
    return qimage

def latents_to_pil(latents: torch.FloatTensor):
    # Code from InvokeAI
    # https://github.com/invoke-ai/InvokeAI/blob/a1cd4834d127641a865438e668c5c7f050e83587/invokeai/backend/generator/base.py#L502

    # originally adapted from code by @erucipe and @keturn here:
    # https://discuss.huggingface.co/t/decoding-latents-to-rgb-without-upscaling/23204/7

    # these updated numbers for v1.5 are from @torridgristle
    v1_5_latent_rgb_factors = torch.tensor(
        [
            #    R        G        B
            [ 0.3444,  0.1385,  0.0670],  # L1
            [ 0.1247,  0.4027,  0.1494],  # L2
            [-0.3192,  0.2513,  0.2103],  # L3
            [-0.1307, -0.1874, -0.7445],  # L4
        ],
        dtype=latents.dtype,
        device=latents.device,
    )

    latent_image = latents[0].permute(1, 2, 0) @ v1_5_latent_rgb_factors
    latents_ubyte = (
        ((latent_image + 1) / 2)
        .clamp(0, 1)  # change scale from -1..1 to 0..1
        .mul(0xFF)  # to 0..255
        .byte()
    ).cpu()

    return Image.fromarray(latents_ubyte.numpy())

class ImageMetadata:
    def __init__(self):
        self.type = 'txt2img'
        self.model = 'stabilityai/stable-diffusion-2-1-base'
        self.scheduler = 'k_euler_a'
        self.path = ''
        self.prompt = ''
        self.negative_prompt = ''
        self.seed = 1
        self.num_inference_steps = 30
        self.guidance_scale = 7.5
        self.width = 512
        self.height = 512
        self.source_path = ''
        self.img_strength = 0.0
        self.gfpgan_enabled = False
        self.gfpgan_strength  = 0.0

    def load_from_settings(self):
        self.type = settings.value('type')
        self.model = settings.value('model')
        self.scheduler = settings.value('scheduler')
        self.prompt = settings.value('prompt')
        self.negative_prompt = settings.value('negative_prompt')
        self.seed = int(settings.value('seed'))
        self.num_inference_steps = int(settings.value('num_inference_steps'))
        self.guidance_scale = float(settings.value('guidance_scale'))
        self.width = int(settings.value('width'))
        self.height = int(settings.value('height'))
        self.source_path = ''
        self.img_strength = 0.0
        if self.type == 'img2img':
            self.source_path = settings.value('source_path')
            self.img_strength = float(settings.value('img_strength'))
        self.gfpgan_enabled = bool_setting('gfpgan_enabled')
        self.gfpgan_strength = 0.0
        if self.gfpgan_enabled:
            self.gfpgan_strength = float(settings.value('gfpgan_strength'))
    
    def load_from_image_info(self, image_info):
        if 'sd-metadata' in image_info:
            sd_metadata = json.loads(image_info['sd-metadata'])
            self.model = sd_metadata.get('model_weights', 'stable-diffusion-2-1-base')
            if 'image' in sd_metadata:
                image_data = sd_metadata['image']
                self.type = image_data.get('type', 'txt2img')
                self.scheduler = image_data.get('sampler', 'k_euler_a')
                self.prompt = image_data.get('prompt', '')
                self.negative_prompt = image_data.get('negative_prompt', '')
                self.seed = int(image_data.get('seed', 5))
                self.steps = int(image_data.get('steps', 30))
                self.guidance_scale = float(image_data.get('cfg_scale', 7.5))
                self.width = int(image_data.get('width', 512))
                self.height = int(image_data.get('height', 512))
                self.source_path = ''
                self.img_strength = 0.0
                if self.type == 'img2img':
                    self.source_path = image_data.get('source_path', '')
                    self.img_strength = float(image_data.get('img_strength', 0.5))
                self.gfpgan_enabled = 'gfpgan_strength' in image_data
                self.gfpgan_strength = 0.0
                if self.gfpgan_enabled:
                    self.gfpgan_strength = float(image_data.get('gfpgan_strength'))

    def save_to_png_info(self, png_info):
        sd_metadata = {
            'model': 'stable diffusion',
            'model_weights': os.path.basename(self.model),
            'model_hash': '',    # TODO
            'app_id': APP_NAME,
            'APP_VERSION': APP_VERSION,
            'image': {
                'prompt': self.prompt,
                'negative_prompt': self.negative_prompt,
                'steps': str(self.num_inference_steps),
                'cfg_scale': str(self.guidance_scale),
                'height': str(self.height),
                'width': str(self.width),
                'seed': str(self.seed),
                'type': self.type,
                'sampler': self.scheduler,
            }
        }
        if self.type == 'img2img':
            sd_metadata['image']['source_path'] = self.source_path
            sd_metadata['image']['img_strength'] = self.img_strength
        if self.gfpgan_enabled:
            sd_metadata['image']['gfpgan_strength'] = self.gfpgan_strength

        png_info.add_text('Dream',
            '"{:s} [{:s}]" -s {:d} -S {:d} -W {:d} -H {:d} -C {:f} -A {:s}'.format(
                self.prompt, self.negative_prompt, self.num_inference_steps, self.seed, self.width, self.height, self.guidance_scale, self.scheduler
            ))
        png_info.add_text('sd-metadata', json.dumps(sd_metadata))


class PromptTextEdit(QPlainTextEdit):
    return_pressed = Signal()

    def __init__(self, desired_lines, placeholder_text, parent=None):
        super().__init__(parent)

        font = self.font()
        font.setPointSize(14)
        self.setFont(font)

        font_metrics = QFontMetrics(font)
        line_height = font_metrics.lineSpacing()
        margins = self.contentsMargins()
        frame_width = self.frameWidth()
        document_margins = self.document().documentMargin()

        self.setFixedHeight(line_height * desired_lines + margins.top() + margins.bottom() + 2 * frame_width + 2 * document_margins)
        self.setPlaceholderText(placeholder_text)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Enter, Qt.Key_Return):
            self.clearFocus()
            self.return_pressed.emit()
        elif key == Qt.Key_Escape:
            self.clearFocus()
        else:
            super().keyPressEvent(event)

        if key in (Qt.Key_Left, Qt.Key_Right):
            event.accept()

class ThumbnailViewer(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.action_send_to_img2img = QAction(QIcon('data/share_icon.png'), 'Send to Image to Image')
        self.action_use_prompt = QAction(QIcon('data/use_prompt_icon.png'), 'Use Prompt')
        self.action_use_seed = QAction(QIcon('data/use_seed_icon.png'), 'Use Seed')
        self.action_use_all = QAction(QIcon('data/use_all_icon.png'), 'Use All')
        self.action_use_initial_image = QAction(QIcon('data/use_initial_image_icon.png'), 'Use Initial Image')
        self.action_delete = QAction(QIcon('data/delete_icon.png'), 'Delete Image')

        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setSpacing(10)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.min_thumbnail_size = 100
        self.max_thumbnail_size = 250
        self.margin = 0

        self.menu = QMenu()
        self.menu.addAction(self.action_send_to_img2img)
        self.menu.addSeparator()
        self.menu.addAction(self.action_use_prompt)
        self.menu.addAction(self.action_use_seed)
        self.menu.addAction(self.action_use_all)
        self.menu.addAction(self.action_use_initial_image)
        self.menu.addSeparator()
        self.menu.addAction(self.action_delete)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_icon_size()

    def visualRect(self, index):
        rect = super().visualRect(index)
        rect.setWidth(self.iconSize().width())
        rect.setHeight(self.iconSize().height())
        return rect

    def add_thumbnail(self, rel_path):
        thumbnail_path = os.path.join(THUMBNAILS_PATH, rel_path)
        if not os.path.exists(thumbnail_path):
            image_path = os.path.join(IMAGES_PATH, rel_path)
            with Image.open(image_path) as image:
                width, height = image.size
                width = width // 2
                height = height // 2
                scaled_image = image.resize((width, height))
                scaled_image.save(thumbnail_path, 'WEBP')

        with Image.open(thumbnail_path) as image:
            pixmap = QPixmap.fromImage(pil_to_qimage(image))
            icon = QIcon(pixmap)
            item = QListWidgetItem()
            item.setIcon(icon)
            item.setData(Qt.UserRole, rel_path)
            self.insertItem(0, item)

    def update_icon_size(self):
        style = QApplication.instance().style()
        scrollbar_width = style.pixelMetric(QStyle.PM_ScrollBarExtent, QStyleOptionSlider())
        available_width = self.width() - scrollbar_width
        num_columns = int((available_width) / (self.min_thumbnail_size))
        num_columns = max(1, num_columns)
        new_icon_size = int((available_width - num_columns) / num_columns)
        new_icon_size = max(self.min_thumbnail_size, min(new_icon_size, self.max_thumbnail_size))

        self.setIconSize(QSize(new_icon_size, new_icon_size))
        self.setGridSize(QSize(new_icon_size, new_icon_size))

    def show_context_menu(self, point):
        self.menu.exec(self.mapToGlobal(point))

class MetadataRow:
    def __init__(self, label_text, multiline=False):
        self.label = QLabel(label_text)
        self.label.setStyleSheet('font-weight: bold; background-color: transparent')

        self.value = QLabel()
        self.value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.value.setStyleSheet('background-color: transparent')
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value.setCursor(Qt.IBeamCursor)

        if multiline:
            self.value.setWordWrap(True)

        self.frame = QFrame()
        self.frame.setContentsMargins(0, 0, 0, 0)
        self.frame.setStyleSheet('background-color: transparent')

        hlayout = QHBoxLayout(self.frame)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.addWidget(self.label)
        hlayout.addWidget(self.value)

class ImageMetadataFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setStyleSheet('background-color: rgba(0, 0, 0, 127);')

        self.path = MetadataRow('Path:')
        self.type = MetadataRow('Type:')
        self.model = MetadataRow('Model:')
        self.scheduler = MetadataRow('Scheduler:')
        self.prompt = MetadataRow('Prompt:', multiline=True)
        self.negative_prompt = MetadataRow('Negative Prompt:', multiline=True)
        self.seed = MetadataRow('Seed:')
        self.num_inference_steps = MetadataRow('Steps:')
        self.guidance_scale = MetadataRow('CFG Scale:')
        self.width = MetadataRow('Width:')
        self.height = MetadataRow('Height:')
        self.source_path = MetadataRow('Source Path:')
        self.img_strength = MetadataRow('Image Strength:')
        self.gfpgan_strength = MetadataRow('Face Restoration:')

        vlayout = QVBoxLayout(self)
        vlayout.addWidget(self.path.frame)
        vlayout.addWidget(self.type.frame)
        vlayout.addWidget(self.scheduler.frame)
        vlayout.addWidget(self.model.frame)
        vlayout.addWidget(self.prompt.frame)
        vlayout.addWidget(self.negative_prompt.frame)
        vlayout.addWidget(self.seed.frame)
        vlayout.addWidget(self.num_inference_steps.frame)
        vlayout.addWidget(self.guidance_scale.frame)
        vlayout.addWidget(self.width.frame)
        vlayout.addWidget(self.height.frame)
        vlayout.addWidget(self.source_path.frame)
        vlayout.addWidget(self.img_strength.frame)
        vlayout.addWidget(self.gfpgan_strength.frame)
        vlayout.addStretch()

    def update(self, metadata):
        self.path.value.setText(metadata.path)
        self.type.value.setText(metadata.type)
        self.scheduler.value.setText(metadata.scheduler)
        self.model.value.setText(metadata.model)
        self.prompt.value.setText(metadata.prompt)
        self.negative_prompt.value.setText(metadata.negative_prompt)
        self.seed.value.setText(str(metadata.seed))
        self.num_inference_steps.value.setText(str(metadata.num_inference_steps))
        self.guidance_scale.value.setText(str(metadata.guidance_scale))
        self.width.value.setText(str(metadata.width))
        self.height.value.setText(str(metadata.height))
        if metadata.type == 'img2img':
            self.source_path.frame.setVisible(True)
            self.source_path.value.setText(metadata.source_path)
            self.img_strength.frame.setVisible(True)
            self.img_strength.value.setText(str(metadata.img_strength))
        else:
            self.source_path.frame.setVisible(False)
            self.img_strength.frame.setVisible(False)
        if metadata.gfpgan_enabled:
            self.gfpgan_strength.frame.setVisible(True)
            self.gfpgan_strength.value.setText(str(metadata.gfpgan_strength))
        else:
            self.gfpgan_strength.frame.setVisible(False)

class ImageViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setMinimumWidth(300)
        self.padding = 5
        self.minimum_image_size = 100
        self.both_images_visible = False
        self.show_preview = True

        self.left_image_path_ = ''
        self.right_image_path_ = ''

        self.left_image = None
        self.right_image = None
        self.preview_image = None

        self.left_label = QLabel(self)
        self.right_label = QLabel(self)
        self.right_controls_frame = QFrame(self)
        self.metadata_frame = ImageMetadataFrame(self)
        self.metadata_frame.setVisible(False)

        icon_size = QSize(24, 24)

        self.send_to_img2img_button = QToolButton()
        self.send_to_img2img_button.setIcon(QIcon('data/share_icon.png'))
        self.send_to_img2img_button.setIconSize(icon_size)
        self.send_to_img2img_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.send_to_img2img_button.setToolTip('Send to Image to Image')
        self.send_to_img2img_button.setToolTipDuration(0)

        self.use_prompt_button = QToolButton()
        self.use_prompt_button.setIcon(QIcon('data/use_prompt_icon.png'))
        self.use_prompt_button.setIconSize(icon_size)
        self.use_prompt_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.use_prompt_button.setToolTip('Use Prompt')
        self.use_prompt_button.setToolTipDuration(0)

        self.use_seed_button = QToolButton()
        self.use_seed_button.setIcon(QIcon('data/use_seed_icon.png'))
        self.use_seed_button.setIconSize(icon_size)
        self.use_seed_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.use_seed_button.setToolTip('Use Seed')
        self.use_seed_button.setToolTipDuration(0)

        self.use_initial_image_button = QToolButton()
        self.use_initial_image_button.setIcon(QIcon('data/use_initial_image_icon.png'))
        self.use_initial_image_button.setIconSize(icon_size)
        self.use_initial_image_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.use_initial_image_button.setToolTip('Use Initial Image')
        self.use_initial_image_button.setToolTipDuration(0)

        self.use_all_button = QToolButton()
        self.use_all_button.setIcon(QIcon('data/use_all_icon.png'))
        self.use_all_button.setIconSize(icon_size)
        self.use_all_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.use_all_button.setToolTip('Use All')
        self.use_all_button.setToolTipDuration(0)

        self.metadata_button = QToolButton()
        self.metadata_button.setIcon(QIcon('data/metadata_icon.png'))
        self.metadata_button.setIconSize(icon_size)
        self.metadata_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.metadata_button.setCheckable(True)
        self.metadata_button.setToolTip('Toggle Image Metadata')
        self.metadata_button.setToolTipDuration(0)
        self.metadata_button.toggled.connect(self.on_metadata_button_changed)

        self.preview_button = QToolButton()
        self.preview_button.setIcon(QIcon('data/preview_icon.png'))
        self.preview_button.setIconSize(icon_size)
        self.preview_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.preview_button.setCheckable(True)
        self.preview_button.setChecked(True)
        self.preview_button.setToolTip('Toggle Preview')
        self.preview_button.setToolTipDuration(0)
        self.preview_button.toggled.connect(self.on_preview_button_changed)

        self.delete_button = QToolButton()
        self.delete_button.setIcon(QIcon('data/delete_icon.png'))
        self.delete_button.setIconSize(icon_size)
        self.delete_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.delete_button.setToolTip('Delete')
        self.delete_button.setToolTipDuration(0)

        right_controls_layout = QHBoxLayout(self.right_controls_frame)
        right_controls_layout.setContentsMargins(0, 0, 0, 0)
        right_controls_layout.setSpacing(0)
        right_controls_layout.addStretch()
        right_controls_layout.addWidget(self.send_to_img2img_button)
        right_controls_layout.addSpacing(8)
        right_controls_layout.addWidget(self.use_prompt_button)
        right_controls_layout.addWidget(self.use_seed_button)
        right_controls_layout.addWidget(self.use_initial_image_button)
        right_controls_layout.addWidget(self.use_all_button)
        right_controls_layout.addSpacing(8)
        right_controls_layout.addWidget(self.metadata_button)
        right_controls_layout.addWidget(self.preview_button)
        right_controls_layout.addSpacing(8)
        right_controls_layout.addWidget(self.delete_button)
        right_controls_layout.addStretch()

        background_color = QApplication.instance().palette().color(QPalette.Base)
        self.setStyleSheet(f'ImageViewer {{ background-color: {background_color.name()}; }}')
        self.setAttribute(Qt.WA_StyledBackground, True)

    def resizeEvent(self, event):
        self.update_images()

    def update_images(self):
        widget_width = self.width()
        widget_height = self.height()
        controls_height = 24

        right_image = self.preview_image if self.preview_image is not None and self.show_preview else self.right_image

        left_image_width = self.left_image.width() if self.left_image is not None else 1
        left_image_height = self.left_image.height() if self.left_image is not None else 1
        right_image_width = right_image.width() if right_image is not None else 1
        right_image_height = right_image.height() if right_image is not None else 1

        if self.both_images_visible:
            available_height = widget_height - controls_height - 4 * self.padding
            available_width = widget_width - 3 * self.padding

            right_height = min(available_height, right_image_height)
            right_width = int(right_image_width * (right_height / right_image_height))

            remaining_width = available_width - right_width
            left_width = min(remaining_width, left_image_width)
            left_height = int(left_image_height * (left_width / left_image_width))

            if left_height > available_height:
                left_height = available_height
                left_width = int(left_image_width * (left_height / left_image_height))

            if left_height < self.minimum_image_size:
                left_height = self.minimum_image_size
                left_width = int(left_image_width * (left_height / left_image_height))
                right_width = available_width - left_width
                right_height = int(right_image_height * (right_width / right_image_width))
                if right_height > available_height:
                    right_height = available_height
                    right_width = int(right_image_width * (right_height / right_image_height))
                if right_height < self.minimum_image_size:
                    right_height = self.minimum_image_size
                    right_width = int(right_image_width * (right_height / right_image_height))

            if self.left_image is not None:
                left_pixmap = QPixmap.fromImage(self.left_image).scaled(left_width, left_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.left_label.setPixmap(left_pixmap)
            else:
                self.left_label.setText('Choose a source image')

            if right_image is not None:
                right_pixmap = QPixmap.fromImage(right_image).scaled(right_width, right_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.right_label.setPixmap(right_pixmap)

            left_x = (widget_width - left_width - right_width - self.padding) // 2
            left_y = controls_height + 2 * self.padding + (available_height - left_height) // 2
            right_x = left_x + self.padding + left_width
            right_y = controls_height + 2 * self.padding + (available_height - right_height) // 2

            self.left_label.setVisible(True)
            self.left_label.setGeometry(left_x, left_y, left_width, left_height)
            self.right_controls_frame.setGeometry(right_x, self.padding, right_width, controls_height)
            self.right_label.setGeometry(right_x, right_y, right_width, right_height)
            self.metadata_frame.setGeometry(right_x, right_y, right_width, right_height)
        else:
            available_height = widget_height - controls_height - 4 * self.padding
            available_width = widget_width - 2 * self.padding

            right_height = min(available_height, right_image_height)
            right_width = int(right_image_width * (right_height / right_image_height))

            if right_image is not None:
                right_pixmap = QPixmap.fromImage(right_image).scaled(right_width, right_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.right_label.setPixmap(right_pixmap)

            right_x = (widget_width - right_width) // 2
            right_y = controls_height + 2 * self.padding + (available_height - right_height) // 2

            self.left_label.setVisible(False)
            self.right_controls_frame.setGeometry(right_x, self.padding, right_width, controls_height)
            self.right_label.setGeometry(right_x, right_y, right_width, right_height)
            self.metadata_frame.setGeometry(right_x, right_y, right_width, right_height)

    def set_both_images_visible(self, both_images_visible):
        self.both_images_visible = both_images_visible
        self.update_images()

    def left_image_path(self):
        return self.left_image_path_
    
    def right_image_path(self):
        return self.right_image_path_

    def set_left_image(self, path):
        fullpath = os.path.join(IMAGES_PATH, path)
        if os.path.exists(fullpath):
            self.left_image_path_ = path
            self.left_image = QImage(fullpath)
        else:
            self.left_image_path_ = ''
            self.left_image = None
        self.update_images()

    def set_right_image(self, path):
        full_path = os.path.join(IMAGES_PATH, path)
        with Image.open(full_path) as image:
            self.metadata = ImageMetadata()
            self.metadata.path = path
            self.metadata.load_from_image_info(image.info)

            self.right_image_path_ = path
            self.right_image = pil_to_qimage(image)

        self.metadata_frame.update(self.metadata)
        self.update_images()

    def set_preview_image(self, preview_image):
        if preview_image is not None:
            self.preview_image = pil_to_qimage(preview_image)
        else:
            self.preview_image = None
        self.update_images()

    def on_metadata_button_changed(self, state):
        self.metadata_frame.setVisible(state)

    def on_preview_button_changed(self, state):
        self.show_preview = state
        self.update_images()

class FloatSliderSpinBox(QWidget):
    def __init__(self, name, initial_value, checkable=False, parent=None):
        super().__init__(parent)

        if checkable:
            self.check_box = QCheckBox(name)
            self.check_box.setChecked(True)
            self.check_box.stateChanged.connect(self.on_check_box_changed)
        else:
            label = QLabel(name)
            label.setAlignment(Qt.AlignCenter)
        frame = QFrame()
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 100)
        self.slider.setValue(initial_value * 100)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.spin_box = QDoubleSpinBox()
        self.spin_box.setAlignment(Qt.AlignCenter)
        self.spin_box.setFixedWidth(80)
        self.spin_box.setRange(0.01, 1.0)
        self.spin_box.setSingleStep(0.01)
        self.spin_box.setDecimals(2)
        self.spin_box.setValue(initial_value)
        self.spin_box.valueChanged.connect(self.on_spin_box_changed)

        hlayout = QHBoxLayout(frame)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.addWidget(self.slider)
        hlayout.addWidget(self.spin_box)

        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(0, 0, 0, 0) 
        vlayout.setSpacing(0)
        if checkable:
            check_box_layout = QHBoxLayout()
            check_box_layout.setAlignment(Qt.AlignCenter)
            check_box_layout.addWidget(self.check_box)
            vlayout.addLayout(check_box_layout)
        else:
            vlayout.addWidget(label)
        vlayout.addWidget(frame)

    def on_check_box_changed(self, state):
        self.slider.setEnabled(state)
        self.spin_box.setEnabled(state)

    def on_slider_changed(self, value):
        decimal_value = value / 100
        self.spin_box.setValue(decimal_value)

    def on_spin_box_changed(self, value):
        slider_value = round(value * 100)
        self.slider.setValue(slider_value)

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowTitle("About")

        layout = QVBoxLayout()

        app_info_label = QLabel(f"{APP_NAME}\nVersion {APP_VERSION}")
        app_info_label.setAlignment(Qt.AlignCenter)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)

        layout.addWidget(app_info_label)
        layout.addWidget(ok_button)

        self.setLayout(layout)
        
class PreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Preferences")
        self.settings = QSettings("settings.ini", QSettings.IniFormat)

        restartLabel = QLabel("Changes to application settings require a restart.")

        self.safety_checker = QCheckBox("Safety Checker")
        self.safety_checker.setChecked(self.settings.value("safety_checker", False, type=bool))

        models_group = QGroupBox()

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Display Name", "Repository ID"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_model)

        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_model)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.add_button)

        models_group_layout = QVBoxLayout(models_group)
        models_group_layout.addWidget(self.table)
        models_group_layout.addLayout(button_layout)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(restartLabel)
        layout.addSpacing(8)
        layout.addWidget(self.safety_checker)
        layout.addWidget(models_group)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.setMinimumWidth(600)

        self.load_models()

    def load_models(self):
        self.settings.beginGroup("Models")
        keys = self.settings.childKeys()
        self.table.setRowCount(len(keys))

        for i, key in enumerate(keys):
            display_name = key
            repo_id = self.settings.value(key)

            self.table.setItem(i, 0, QTableWidgetItem(display_name))
            self.table.setItem(i, 1, QTableWidgetItem(repo_id))

        self.settings.endGroup()

    def remove_model(self):
        current_row = self.table.currentRow()

        if current_row == -1:
            QMessageBox.warning(self, "Warning", "Please select a model to remove.")
            return

        self.table.removeRow(current_row)

    def add_model(self):
        add_dialog = QDialog(self)
        add_dialog.setWindowTitle("Add Model")

        vbox = QVBoxLayout()

        hbox1 = QHBoxLayout()
        hbox1.addWidget(QLabel("Display Name:"))
        display_name_edit = QLineEdit()
        hbox1.addWidget(display_name_edit)
        vbox.addLayout(hbox1)

        hbox2 = QHBoxLayout()
        hbox2.addWidget(QLabel("Repository ID:"))
        repo_id_edit = QLineEdit()
        hbox2.addWidget(repo_id_edit)
        vbox.addLayout(hbox2)

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(add_dialog.accept)
        button_box.rejected.connect(add_dialog.reject)
        vbox.addWidget(button_box)

        add_dialog.setLayout(vbox)

        result = add_dialog.exec()
        if result == QDialog.Accepted:
            display_name = display_name_edit.text()
            repo_id = repo_id_edit.text()

            if not display_name or not repo_id:
                return

            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(display_name))
            self.table.setItem(row, 1, QTableWidgetItem(repo_id))

    def accept(self):
        self.settings.setValue("safety_checker", self.safety_checker.isChecked())

        self.settings.beginGroup("Models")
        self.settings.remove("")

        for row in range(self.table.rowCount()):
            display_name = self.table.item(row, 0).text()
            repo_id = self.table.item(row, 1).text()

            self.settings.setValue(display_name, repo_id)

        self.settings.endGroup()

        super().accept()
        
class InitThread(QThread):
    task_complete = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            self.run_()
        except Exception as e:
            traceback.print_exc()        

        self.task_complete.emit()
 
    def run_(self):
        warnings.filterwarnings('ignore')

        dtype = torch.float32
        device = 'mps'
        model = os.path.expanduser(settings.value('model'))
        if bool_setting('safety_checker'):
            base_pipe = StableDiffusionPipeline.from_pretrained(model, torch_dtype=dtype)
        else:
            base_pipe = StableDiffusionPipeline.from_pretrained(model, safety_checker=None, torch_dtype=dtype, requires_safety_checker=False)

        global pipes
        pipes['txt2img'] = base_pipe
        pipes['img2img'] = StableDiffusionImg2ImgPipeline(**base_pipe.components, requires_safety_checker=False)

        for type in pipes:
            pipe = pipes[type]
            pipe.enable_attention_slicing()
            pipes[type] = pipe.to(device)

        global gfpgan
        gfpgan = GFPGANer(
            model_path='data/GFPGANv1.4.pth',
            upscale=1,
            arch='clean',
            channel_multiplier=2,
            bg_upsampler=None,
        )

        warnings.resetwarnings()

class CancelThreadException(Exception):
    pass

class GenerateThread(QThread):
    task_progress = Signal(int)
    image_preview = Signal(Image.Image)
    image_complete = Signal(str)
    task_complete = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.cancel = False
        self.type = settings.value('type')
        self.metadata = ImageMetadata()
        self.metadata.load_from_settings()
        self.num_images_per_prompt = int(settings.value('num_images_per_prompt', 1))

    def run(self):
        try:
            self.run_()
        except CancelThreadException:
            pass
        except Exception as e:
            traceback.print_exc()

        self.task_complete.emit()
 
    def run_(self):
        # type
        pipe = pipes[self.type]
        if self.type == 'img2img':
            full_path = os.path.join(IMAGES_PATH, self.metadata.source_path)
            f = open(full_path, 'rb')
            source_image = Image.open(f).convert('RGB')
            source_image = source_image.resize((self.metadata.width, self.metadata.height))
            f.close()

        # scheduler
        pipe.scheduler = schedulers[self.metadata.scheduler].from_config(pipe.scheduler.config)

        # generator
        generator = torch.Generator().manual_seed(self.metadata.seed)

        # prompt weighting
        compel_proc = Compel(tokenizer=pipe.tokenizer, text_encoder=pipe.text_encoder)
        prompt_embeds = compel_proc(self.metadata.prompt)
        negative_prompt_embeds = compel_proc(self.metadata.negative_prompt)

        # generate
        if self.type == 'txt2img':
            images = pipe(
                width=self.metadata.width,
                height=self.metadata.height,
                num_inference_steps=self.metadata.num_inference_steps,
                guidance_scale=self.metadata.guidance_scale,
                num_images_per_prompt=self.num_images_per_prompt,
                generator=generator,
                prompt_embeds=prompt_embeds,
                negative_prompt_embeds=negative_prompt_embeds,
                callback=self.generate_callback,
            ).images
        elif self.type == 'img2img':
            images = pipe(
                image=source_image,
                strength=self.metadata.img_strength,
                num_inference_steps=self.metadata.num_inference_steps,
                guidance_scale=self.metadata.guidance_scale,
                num_images_per_prompt=self.num_images_per_prompt,
                generator=generator,
                prompt_embeds=prompt_embeds,
                negative_prompt_embeds=negative_prompt_embeds,
                callback=self.generate_callback,
            ).images

        steps = self.compute_total_steps()
        step = steps - self.num_images_per_prompt
        for image in images:
            # GFPGAN
            if self.metadata.gfpgan_strength > 0.0:
                bgr_image_array = np.array(image, dtype=np.uint8)[..., ::-1]

                _, _, restored_img = gfpgan.enhance(
                    bgr_image_array,
                    has_aligned=False,
                    only_center_face=False,
                    paste_back=True,
                )

                image2 = Image.fromarray(restored_img[..., ::-1])

                if self.metadata.gfpgan_strength < 1.0:
                    if image2.size != image.size:
                        image = image.resize(image2.size)
                    image = Image.blend(image, image2, self.metadata.gfpgan_strength)
                else:
                    image = image2

            progress_amount = (step+1) * 100 / steps
            step = step + 1
            self.task_progress.emit(progress_amount)

            # Output
            collection_path = settings.value('collection')
            image_files = sorted([file for file in os.listdir(os.path.join(IMAGES_PATH, collection_path)) if file.lower().endswith(('.webp', '.png', '.jpg', '.jpeg', '.gif', '.bmp'))])

            next_image_id = 0
            for image_file in image_files:
                match = re.match(r'(\d+)\.png', image_file)
                if match:
                    next_image_id = max(next_image_id, int(match.group(1)))
            next_image_id = next_image_id + 1

            output_path = os.path.join(collection_path, '{:05d}.png'.format(next_image_id))
            full_path = os.path.join(IMAGES_PATH, output_path)

            png_info = PngImagePlugin.PngInfo()
            self.metadata.save_to_png_info(png_info)
            image.save(full_path, pnginfo=png_info)

            self.image_complete.emit(output_path)

    def generate_callback(self, step: int, timestep: int, latents: torch.FloatTensor):
        if self.cancel:
            raise CancelThreadException()
        steps = self.compute_total_steps()
        progress_amount = (step+1) * 100 / steps
        self.task_progress.emit(progress_amount)

        pil_image = latents_to_pil(latents)
        pil_image = pil_image.resize((pil_image.size[0] * 8, pil_image.size[1] * 8), Image.NEAREST)
        self.image_preview.emit(pil_image)

    def compute_total_steps(self):
        if self.type == 'img2img':
            steps = int(self.metadata.num_inference_steps * self.metadata.img_strength)
        else:
            steps = self.metadata.num_inference_steps
        steps = steps + self.num_images_per_prompt
        return steps

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.init_thread = None
        self.generate_thread = None
        self.active_thread_count = 0

        self.setFocusPolicy(Qt.ClickFocus)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Menubar
        menu_bar = QMenuBar(self)

        action_about = QAction("About", self)
        action_about.triggered.connect(self.show_about_dialog)
        action_preferences = QAction("Preferences", self)
        action_preferences.setMenuRole(QAction.MenuRole.PreferencesRole)
        action_preferences.triggered.connect(self.show_preferences_dialog)

        action_generate_image = QAction("Generate Image", self)
        action_generate_image.setShortcut(Qt.CTRL | Qt.Key_Return)
        action_cancel_generation = QAction("Cancel Generation", self)
        action_cancel_generation.setShortcut(Qt.SHIFT | Qt.Key_X)
        action_send_to_img2img = QAction("Send to Image to Image", self)
        action_use_prompt = QAction("Use Prompt", self)
        action_use_prompt.setShortcut(Qt.Key_P)
        action_use_seed = QAction("Use Seed", self)
        action_use_seed.setShortcut(Qt.Key_S)
        action_use_initial_image = QAction("Use Initial Image", self)
        action_use_all = QAction("Use All", self)
        action_use_all.setShortcut(Qt.Key_A)
        action_metadata = QAction("Metadata", self)
        action_metadata.setShortcut(Qt.Key_I)
        action_delete_image = QAction("Delete Image", self)
        action_delete_image.setShortcut(Qt.Key_Delete)
        action_previous_image = QAction("Previous Image", self)
        action_previous_image.setShortcut(Qt.Key_Left)
        action_next_image = QAction("Next Image", self)
        action_next_image.setShortcut(Qt.Key_Right)

        app_menu = QMenu("Application", self)
        menu_bar.addMenu(app_menu)
        app_menu.addAction(action_about)
        app_menu.addSeparator()
        app_menu.addAction(action_preferences)

        image_menu = QMenu("Image", menu_bar)
        image_menu.addAction(action_generate_image)
        image_menu.addAction(action_cancel_generation)
        image_menu.addSeparator()
        image_menu.addAction(action_send_to_img2img)
        image_menu.addAction(action_use_prompt)
        image_menu.addAction(action_use_seed)
        image_menu.addAction(action_use_initial_image)
        image_menu.addAction(action_use_all)
        image_menu.addAction(action_metadata)
        image_menu.addSeparator()
        image_menu.addAction(action_delete_image)
        image_menu.addSeparator()

        action_generate_image.triggered.connect(self.on_generate_image)
        action_cancel_generation.triggered.connect(self.on_cancel_generation)
        action_send_to_img2img.triggered.connect(lambda: self.on_send_to_img2img(self.image_viewer.metadata))
        action_use_prompt.triggered.connect(lambda: self.on_use_prompt(self.image_viewer.metadata))
        action_use_seed.triggered.connect(lambda: self.on_use_seed(self.image_viewer.metadata))
        action_use_initial_image.triggered.connect(lambda: self.on_use_initial_image(self.image_viewer.metadata))
        action_use_all.triggered.connect(lambda: self.on_use_all(self.image_viewer.metadata))
        action_metadata.triggered.connect(lambda: self.image_viewer.metadata_button.toggle())
        action_delete_image.triggered.connect(lambda: self.on_delete(self.image_viewer.metadata))

        # Add the menu to the menu bar
        menu_bar.addMenu(image_menu)

        # Set the menu bar to the main window
        self.setMenuBar(menu_bar)

        # Modes
        mode_toolbar = QToolBar()
        mode_toolbar.setMovable(False)
        self.addToolBar(Qt.LeftToolBarArea, mode_toolbar)

        txt2img_button = QToolButton()
        txt2img_button.setIcon(QIcon('data/txt2img_icon.png'))
        txt2img_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        txt2img_button.setCheckable(True)
        txt2img_button.setAutoExclusive(True)
        txt2img_button.setToolTip('Text To Image')
        txt2img_button.setToolTipDuration(0)

        img2img_button = QToolButton()
        img2img_button.setIcon(QIcon('data/img2img_icon.png'))
        img2img_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        img2img_button.setCheckable(True)
        img2img_button.setAutoExclusive(True)
        img2img_button.setToolTip('Image To Image')
        img2img_button.setToolTipDuration(0)

        mode_toolbar.addWidget(txt2img_button)
        mode_toolbar.addWidget(img2img_button)

        self.button_group = QButtonGroup()
        self.button_group.addButton(txt2img_button, 0)
        self.button_group.addButton(img2img_button, 1)
        self.button_group.idToggled.connect(self.on_mode_changed)

        # Configuration controls
        config_frame = QFrame()
        config_frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        config_frame.setContentsMargins(0, 0, 0, 0)

        self.model_combo_box = QComboBox()
        settings.beginGroup('Models')
        for key in settings.childKeys():
            value = settings.value(key)
            index = self.model_combo_box.addItem(key, value)
        settings.endGroup()
        index = self.model_combo_box.findData(settings.value('model'))
        if index != -1:
            self.model_combo_box.setCurrentIndex(index)
        self.model_combo_box.currentIndexChanged.connect(self.on_model_combo_box_changed)

        self.prompt_edit = PromptTextEdit(8, 'Prompt')
        self.prompt_edit.setPlainText(settings.value('prompt'))
        self.prompt_edit.return_pressed.connect(self.on_generate_image)
        self.negative_prompt_edit = PromptTextEdit(3, 'Negative Prompt')
        self.negative_prompt_edit.setPlainText(settings.value('negative_prompt'))
        self.negative_prompt_edit.return_pressed.connect(self.on_generate_image)

        self.generate_button = QPushButton('Generate')
        self.generate_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.generate_button.clicked.connect(self.on_generate_image)

        cancel_button = QPushButton()
        cancel_button.setIcon(QIcon('data/cancel_icon.png'))
        cancel_button.setToolTip('Cancel')
        cancel_button.clicked.connect(self.on_cancel_generation)

        generate_hlayout = QHBoxLayout()
        generate_hlayout.setContentsMargins(0, 0, 0, 0)
        generate_hlayout.addWidget(self.generate_button)
        generate_hlayout.addWidget(cancel_button)

        controls_frame = QFrame()
        controls_frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        num_images_label = QLabel('Images')
        num_images_label.setAlignment(Qt.AlignCenter)
        self.num_images_spin_box = QSpinBox()
        self.num_images_spin_box.setAlignment(Qt.AlignCenter)
        self.num_images_spin_box.setFixedWidth(80)
        self.num_images_spin_box.setMinimum(1)
        self.num_images_spin_box.setValue(int(settings.value('num_images_per_prompt')))
        num_steps_label = QLabel('Steps')
        num_steps_label.setAlignment(Qt.AlignCenter)
        self.num_steps_spin_box = QSpinBox()
        self.num_steps_spin_box.setAlignment(Qt.AlignCenter)
        self.num_steps_spin_box.setFixedWidth(80)
        self.num_steps_spin_box.setMinimum(1)
        self.num_steps_spin_box.setValue(int(settings.value('num_inference_steps')))
        guidance_scale_label = QLabel('CFG Scale')
        guidance_scale_label.setAlignment(Qt.AlignCenter)
        self.guidance_scale_spin_box = QDoubleSpinBox()
        self.guidance_scale_spin_box.setAlignment(Qt.AlignCenter)
        self.guidance_scale_spin_box.setFixedWidth(80)
        self.guidance_scale_spin_box.setSingleStep(0.5)
        self.guidance_scale_spin_box.setMinimum(0.5)
        self.guidance_scale_spin_box.setValue(float(settings.value('guidance_scale')))
        width_label = QLabel('Width')
        width_label.setAlignment(Qt.AlignCenter)
        self.width_spin_box = QSpinBox()
        self.width_spin_box.setAlignment(Qt.AlignCenter)
        self.width_spin_box.setFixedWidth(80)
        self.width_spin_box.setSingleStep(64)
        self.width_spin_box.setMinimum(64)
        self.width_spin_box.setMaximum(1024)
        self.width_spin_box.setValue(int(settings.value('width')))
        height_label = QLabel('Height')
        height_label.setAlignment(Qt.AlignCenter)
        self.height_spin_box = QSpinBox()
        self.height_spin_box.setAlignment(Qt.AlignCenter)
        self.height_spin_box.setFixedWidth(80)
        self.height_spin_box.setSingleStep(64)
        self.height_spin_box.setMinimum(64)
        self.height_spin_box.setMaximum(1024)
        self.height_spin_box.setValue(int(settings.value('height')))
        scheduler_label = QLabel('Scheduler')
        scheduler_label.setAlignment(Qt.AlignCenter)
        self.scheduler_combo_box = QComboBox()
        self.scheduler_combo_box.addItems(schedulers.keys())
        self.scheduler_combo_box.setFixedWidth(120)
        self.scheduler_combo_box.setCurrentText(settings.value('scheduler'))

        controls_grid = QGridLayout(controls_frame)
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setVerticalSpacing(2)
        controls_grid.setRowMinimumHeight(2, 10)
        controls_grid.addWidget(num_images_label, 0, 0)
        controls_grid.addWidget(self.num_images_spin_box, 1, 0)
        controls_grid.addWidget(num_steps_label, 0, 1)
        controls_grid.addWidget(self.num_steps_spin_box, 1, 1)
        controls_grid.addWidget(guidance_scale_label, 0, 2)
        controls_grid.addWidget(self.guidance_scale_spin_box, 1, 2)
        controls_grid.setAlignment(self.guidance_scale_spin_box, Qt.AlignCenter)
        controls_grid.addWidget(width_label, 3, 0)
        controls_grid.addWidget(self.width_spin_box, 4, 0)
        controls_grid.addWidget(height_label, 3, 1)
        controls_grid.addWidget(self.height_spin_box, 4, 1)
        controls_grid.addWidget(scheduler_label, 3, 2)
        controls_grid.addWidget(self.scheduler_combo_box, 4, 2)

        self.manual_seed_check_box = QCheckBox('Manual Seed')

        self.seed_frame = QFrame()
        self.seed_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.seed_lineedit = QLineEdit()
        self.seed_lineedit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.seed_lineedit.setText(str(settings.value('seed')))
        seed_random_button = QPushButton('New')
        seed_random_button.clicked.connect(self.on_seed_random_clicked)

        seed_hlayout = QHBoxLayout(self.seed_frame)
        seed_hlayout.setContentsMargins(0, 0, 0, 0)
        seed_hlayout.addWidget(self.seed_lineedit)
        seed_hlayout.addWidget(seed_random_button)

        seed_vlayout = QVBoxLayout()
        seed_vlayout.setContentsMargins(0, 0, 0, 0) 
        seed_vlayout.setSpacing(0)
        seed_check_box_layout = QHBoxLayout()
        seed_check_box_layout.setAlignment(Qt.AlignCenter)
        seed_check_box_layout.addWidget(self.manual_seed_check_box)
        seed_vlayout.addLayout(seed_check_box_layout)
        seed_vlayout.addWidget(self.seed_frame)

        manual_seed = bool_setting('manual_seed')
        self.seed_frame.setEnabled(manual_seed)
        self.manual_seed_check_box.setChecked(manual_seed)
        self.manual_seed_check_box.stateChanged.connect(self.on_manual_seed_check_box_changed)

        self.img_strength = FloatSliderSpinBox('Image Strength', float(settings.value('img_strength')))
        self.img_strength.setVisible(False)

        self.gfpgan_strength = FloatSliderSpinBox('Face Restoration', float(settings.value('gfpgan_strength')), checkable=True)
        self.gfpgan_strength.check_box.setChecked(bool_setting('gfpgan_enabled'))

        config_layout = QVBoxLayout(config_frame)
        config_layout.setContentsMargins(0, 0, 0, 0) 
        config_layout.addWidget(self.model_combo_box)
        config_layout.addWidget(self.prompt_edit)
        config_layout.addWidget(self.negative_prompt_edit)
        config_layout.addLayout(generate_hlayout)
        config_layout.addWidget(controls_frame)
        config_layout.addLayout(seed_vlayout)
        config_layout.addWidget(self.img_strength)
        config_layout.addWidget(self.gfpgan_strength)
        config_layout.addStretch()

        # Image viewer
        self.image_viewer = ImageViewer()
        self.image_viewer.send_to_img2img_button.pressed.connect(lambda: self.on_send_to_img2img(self.image_viewer.metadata))
        self.image_viewer.use_prompt_button.pressed.connect(lambda: self.on_use_prompt(self.image_viewer.metadata))
        self.image_viewer.use_seed_button.pressed.connect(lambda: self.on_use_seed(self.image_viewer.metadata))
        self.image_viewer.use_initial_image_button.pressed.connect(lambda: self.on_use_initial_image(self.image_viewer.metadata))
        self.image_viewer.use_all_button.pressed.connect(lambda: self.on_use_all(self.image_viewer.metadata))
        self.image_viewer.delete_button.pressed.connect(lambda: self.on_delete(self.image_viewer.metadata))

        #  Thumbnails
        thumbnail_frame = QFrame()
        thumbnail_frame.setContentsMargins(0, 0, 0, 0)

        self.collection_combobox = QComboBox()

        self.thumbnail_viewer = ThumbnailViewer()
        self.thumbnail_viewer.itemSelectionChanged.connect(self.on_thumbnail_selection_change)
        self.thumbnail_viewer.action_send_to_img2img.triggered.connect(lambda: self.on_send_to_img2img(self.get_thumbnail_metadata()))
        self.thumbnail_viewer.action_use_prompt.triggered.connect(lambda: self.on_use_prompt(self.get_thumbnail_metadata()))
        self.thumbnail_viewer.action_use_seed.triggered.connect(lambda: self.on_use_seed(self.get_thumbnail_metadata()))
        self.thumbnail_viewer.action_use_initial_image.triggered.connect(lambda: self.on_use_initial_image(self.get_thumbnail_metadata()))
        self.thumbnail_viewer.action_use_all.triggered.connect(lambda: self.on_use_all(self.get_thumbnail_metadata()))
        self.thumbnail_viewer.action_delete.triggered.connect(lambda: self.on_delete(self.get_thumbnail_metadata()))

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setWidget(self.thumbnail_viewer)

        thumbnail_layout = QVBoxLayout(thumbnail_frame)
        thumbnail_layout.setContentsMargins(0, 0, 0, 0)
        thumbnail_layout.addWidget(self.collection_combobox)
        thumbnail_layout.addWidget(scroll_area)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.image_viewer)
        splitter.addWidget(thumbnail_frame)
        splitter.setStretchFactor(0, 1)  # left widget
        splitter.setStretchFactor(1, 0)  # right widget

        palette = QApplication.instance().palette()
        background_color = palette.color(QPalette.Window)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setMinimum(0)

        hlayout = QHBoxLayout()
        hlayout.setContentsMargins(8, 2, 8, 8)
        hlayout.setSpacing(8)
        hlayout.addWidget(config_frame)
        hlayout.addWidget(splitter)

        vlayout = QVBoxLayout(central_widget)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(0)
        vlayout.addWidget(self.progress_bar)
        vlayout.addLayout(hlayout)

        self.setWindowTitle(APP_NAME)
        self.setGeometry(100, 100, 1200, 600)

        # Gather collections
        collections = sorted([entry for entry in os.listdir(IMAGES_PATH) if os.path.isdir(os.path.join(IMAGES_PATH, entry))])
        if not collections:
            os.makedirs(os.path.join(IMAGES_PATH, 'outputs'))
            collections = ['outputs']

        self.collection_combobox.addItems(collections)
        self.collection_combobox.setCurrentText(settings.value('collection'))
        self.collection_combobox.currentIndexChanged.connect(self.on_collection_changed)
        self.on_collection_changed()

        # Apply settings that impact other controls
        if settings.value('source_path') != '':
            self.image_viewer.set_left_image(settings.value('source_path'))
        self.set_type(settings.value('type'))

        self.init_model()

    def init_model(self):
        self.generate_button.setEnabled(False)
        self.model_combo_box.setEnabled(False)            
        self.update_progress(0, 0)
        self.init_thread = InitThread(self)
        self.init_thread.task_complete.connect(self.init_complete)
        self.init_thread.start()

    def init_complete(self):
        self.generate_button.setEnabled(True)
        self.model_combo_box.setEnabled(True)
        self.update_progress(None)
        self.init_thread = None

    def show_about_dialog(self):
        about_dialog = AboutDialog()
        about_dialog.exec()

    def show_preferences_dialog(self):
        dialog = PreferencesDialog(self)
        dialog.exec()
        
    def set_type(self, type):
        self.type = type
        if self.type == 'txt2img':
            self.button_group.button(0).setChecked(True)
        elif self.type == 'img2img':
            self.button_group.button(1).setChecked(True)

    def on_mode_changed(self, button_id, checked):
        if not checked:
            return
        if button_id == 0:
            self.type = 'txt2img'
            self.img_strength.setVisible(False)
            self.image_viewer.set_both_images_visible(False)
        elif button_id == 1:
            self.type = 'img2img'
            self.img_strength.setVisible(True)
            self.image_viewer.set_both_images_visible(True)

    def on_model_combo_box_changed(self, index):
        model = self.sender().itemData(index)
        settings.setValue('model', model)
        self.init_model()

    def on_cancel_generation(self):
        if self.generate_thread:
            self.generate_thread.cancel = True

    def on_generate_image(self):
        self.update_progress(0, 0)
        self.generate_button.setEnabled(False)
        self.model_combo_box.setEnabled(False)

        if not self.manual_seed_check_box.isChecked():
            self.randomize_seed()

        settings.setValue('collection', self.collection_combobox.currentText())
        settings.setValue('type', self.type)
        settings.setValue('scheduler', self.scheduler_combo_box.currentText())
        settings.setValue('prompt', self.prompt_edit.toPlainText())
        settings.setValue('negative_prompt', self.negative_prompt_edit.toPlainText())
        settings.setValue('manual_seed', self.manual_seed_check_box.isChecked())
        settings.setValue('seed', self.seed_lineedit.text())
        settings.setValue('num_images_per_prompt', self.num_images_spin_box.value())
        settings.setValue('num_inference_steps', self.num_steps_spin_box.value())
        settings.setValue('guidance_scale', self.guidance_scale_spin_box.value())
        settings.setValue('width', self.width_spin_box.value())
        settings.setValue('height', self.height_spin_box.value())
        settings.setValue('source_path', self.image_viewer.left_image_path())
        settings.setValue('img_strength', self.img_strength.spin_box.value())
        settings.setValue('gfpgan_enabled', self.gfpgan_strength.check_box.isChecked())
        settings.setValue('gfpgan_strength', self.gfpgan_strength.spin_box.value())

        self.generate_thread = GenerateThread(self)
        self.generate_thread.task_progress.connect(self.update_progress)
        self.generate_thread.image_preview.connect(self.image_preview)
        self.generate_thread.image_complete.connect(self.image_complete)
        self.generate_thread.task_complete.connect(self.generate_complete)
        self.generate_thread.start()

    def update_progress(self, progress_amount, maximum_amount=100):
        self.progress_bar.setMaximum(maximum_amount)
        if maximum_amount == 0:
            self.progress_bar.setStyleSheet('QProgressBar:chunk { background-color: grey; }')
        else:
            self.progress_bar.setStyleSheet('QProgressBar:chunk { background-color: blue; }')
        if progress_amount is not None:
            self.progress_bar.setValue(progress_amount)
        else:
            self.progress_bar.setValue(0)

        if sys.platform == 'darwin':
            sharedApplication = NSApplication.sharedApplication()
            dockTile = sharedApplication.dockTile()
            if maximum_amount == 0:
                dockTile.setBadgeLabel_('...')
            elif progress_amount is not None:
                dockTile.setBadgeLabel_('{:d}%'.format(progress_amount))
            else:
                dockTile.setBadgeLabel_(None)

    def image_preview(self, preview_image):
        self.image_viewer.set_preview_image(preview_image)
    
    def image_complete(self, output_path):
        self.thumbnail_viewer.add_thumbnail(output_path)
        self.thumbnail_viewer.setCurrentRow(0)
        self.image_viewer.set_right_image(output_path)

    def generate_complete(self):
        self.generate_button.setEnabled(True)
        self.model_combo_box.setEnabled(True)
        self.update_progress(None)
        self.image_viewer.set_preview_image(None)
        self.generate_thread = None

    def randomize_seed(self):
        seed = random.randint(0, 0x7fff_ffff_ffff_ffff)
        self.seed_lineedit.setText(str(seed))

    def on_manual_seed_check_box_changed(self, state):
        self.seed_frame.setEnabled(state)

    def on_seed_random_clicked(self):
        self.randomize_seed()

    def on_thumbnail_selection_change(self):
        selected_items = self.thumbnail_viewer.selectedItems()
        for item in selected_items:
            rel_path = item.data(Qt.UserRole)
            self.image_viewer.set_right_image(rel_path)

    def get_thumbnail_metadata(self):
        item = self.thumbnail_viewer.currentItem()
        if item is not None:
            rel_path = item.data(Qt.UserRole)
            full_path = os.path.join(IMAGES_PATH, rel_path)
            with Image.open(full_path) as image:
                metadata = ImageMetadata()
                metadata.path = rel_path
                metadata.load_from_image_info(image.info)
                return metadata
        return None
    
    def on_send_to_img2img(self, image_metadata):
        if image_metadata is not None:
            self.image_viewer.set_left_image(image_metadata.path)
            self.set_type('img2img')
    
    def on_use_prompt(self, image_metadata):
        if image_metadata is not None:
            self.prompt_edit.setPlainText(image_metadata.prompt)
            self.negative_prompt_edit.setPlainText(image_metadata.negative_prompt)

    def on_use_seed(self, image_metadata):
        if image_metadata is not None:
            self.manual_seed_check_box.setChecked(True)
            self.seed_lineedit.setText(str(image_metadata.seed))

    def on_use_initial_image(self, image_metadata):
        if image_metadata is not None:
            self.image_viewer.set_left_image(image_metadata.source_path)
            self.img_strength.spin_box.setValue(image_metadata.img_strength)
            self.set_type('img2img')
 
    def on_use_all(self, image_metadata):
        if image_metadata is not None:
            self.prompt_edit.setPlainText(image_metadata.prompt)
            self.negative_prompt_edit.setPlainText(image_metadata.negative_prompt)
            self.manual_seed_check_box.setChecked(True)
            self.seed_lineedit.setText(str(image_metadata.seed))
            self.num_steps_spin_box.setValue(image_metadata.num_inference_steps)
            self.guidance_scale_spin_box.setValue(image_metadata.guidance_scale)
            self.width_spin_box.setValue(image_metadata.width)
            self.height_spin_box.setValue(image_metadata.height)
            self.scheduler_combo_box.setCurrentText(image_metadata.scheduler)
            if image_metadata.type == 'img2img':
                self.image_viewer.set_left_image(image_metadata.source_path)
                self.img_strength.spin_box.setValue(image_metadata.img_strength)
            if image_metadata.gfpgan_enabled:
                self.gfpgan_strength.check_box.setChecked(True)
                self.gfpgan_strength.spin_box.setValue(image_metadata.gfpgan_strength)
            else:
                self.gfpgan_strength.check_box.setChecked(False)
            self.set_type(image_metadata.type)

    def on_delete(self, image_metadata):
        if image_metadata is not None:
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle('Confirm Delete')
            message_box.setText('Are you sure you want to delete this image?')
            message_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            message_box.setDefaultButton(QMessageBox.No)

            result = message_box.exec()
            if result == QMessageBox.Yes:
                full_path = os.path.join(IMAGES_PATH, image_metadata.path)
                if sys.platform == 'darwin':
                    # Move file to trash
                    file_url = NSURL.fileURLWithPath_(full_path)
                    NSWorkspace.sharedWorkspace().recycleURLs_completionHandler_([file_url], None)
                else:
                    os.remove(full_path)

                full_path = os.path.join(THUMBNAILS_PATH, image_metadata.path)
                os.remove(full_path)

                item = self.thumbnail_viewer.currentItem()
                self.thumbnail_viewer.takeItem(self.thumbnail_viewer.row(item))

    def on_previous_image(self):
        next_row = self.thumbnail_viewer.currentRow() - 1
        if next_row >= 0:
            self.thumbnail_viewer.setCurrentRow(next_row)

    def on_next_image(self):
        next_row = self.thumbnail_viewer.currentRow() + 1
        if next_row < self.thumbnail_viewer.count():
            self.thumbnail_viewer.setCurrentRow(next_row)

    def on_collection_changed(self):
        collection_path = self.collection_combobox.currentText()
        self.thumbnail_viewer.clear()

        os.makedirs(os.path.join(THUMBNAILS_PATH, collection_path), exist_ok=True)
        image_files = sorted([file for file in os.listdir(os.path.join(IMAGES_PATH, collection_path)) if file.lower().endswith(('.webp', '.png', '.jpg', '.jpeg', '.gif', '.bmp'))])

        for image_file in image_files:
            image_path = os.path.join(collection_path, image_file)
            self.thumbnail_viewer.add_thumbnail(image_path)

        self.thumbnail_viewer.setCurrentRow(0)

    def hide_if_thread_running(self):
        if self.init_thread:
            self.active_thread_count = self.active_thread_count + 1
            self.init_thread.finished.connect(self.thread_finished)
        if self.generate_thread:
            self.active_thread_count = self.active_thread_count + 1
            self.generate_thread.cancel = True
            self.generate_thread.finished.connect(self.thread_finished)

        if self.active_thread_count > 0:
            self.hide()
            return True
        else:
            return False

    def thread_finished(self):
        self.active_thread_count = self.active_thread_count - 1
        if self.active_thread_count == 0:
            QApplication.instance().quit()

    def closeEvent(self, event):
        if self.hide_if_thread_running():
            event.ignore()
        else:
            event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left:
            self.on_previous_image()
        elif key == Qt.Key_Right:
            self.on_next_image()
        else:
            super().keyPressEvent(event)

class Application(QApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Settings
        global settings
        settings = QSettings('settings.ini', QSettings.IniFormat)
        set_default_setting('safety_checker', True)
        set_default_setting('collection', 'outputs')
        set_default_setting('type', 'txt2img')
        set_default_setting('scheduler', 'k_euler_a')
        set_default_setting('model', 'stabilityai/stable-diffusion-2-1-base')
        set_default_setting('prompt', '')
        set_default_setting('negative_prompt', '')
        set_default_setting('manual_seed', False)
        set_default_setting('seed', 1)
        set_default_setting('num_images_per_prompt', 1)
        set_default_setting('num_inference_steps', 30)
        set_default_setting('guidance_scale', 7.5)
        set_default_setting('width', 512)
        set_default_setting('height', 512)
        set_default_setting('source_path', '')
        set_default_setting('img_strength', 0.5)
        set_default_setting('gfpgan_enabled', False)
        set_default_setting('gfpgan_strength', 0.8)
        settings.beginGroup('Models')
        set_default_setting('Stable Diffusion v2-1-base', 'stabilityai/stable-diffusion-2-1-base')
        settings.endGroup()

        self.setWindowIcon(QIcon('data/app_icon.png'))
        self.setApplicationName(APP_NAME)
        self.setStyleSheet('''
        QToolButton {
            background-color: rgba(50, 50, 50, 255);
        }
        QToolButton:hover {
            background-color: darkgrey;
        }
        QToolButton:checked {
            background-color: darkblue;
        }
        QToolButton:pressed {
            background-color: darkblue;
        }
        ''')
        self.main_window = MainWindow()
        self.main_window.show()

    def event(self, event):
        if event.type() == QEvent.Quit:
            if self.main_window.hide_if_thread_running():
                return False
        return super().event(event)
    
def main():
    os.makedirs(IMAGES_PATH, exist_ok=True)
    os.makedirs(THUMBNAILS_PATH, exist_ok=True)

    if sys.platform == 'darwin':
        bundle = NSBundle.mainBundle()
        info_dict = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        info_dict['CFBundleName'] = APP_NAME

    app = Application(sys.argv)
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
