from mmdet.core import bbox2result
from mmdet.models import TwoStageDetector

from qdtrack.core import track2result
from ..builder import MODELS, build_tracker


@MODELS.register_module()
class QuasiDenseFasterRCNN(TwoStageDetector):

    def __init__(self, tracker=None, freeze_detector=False, *args, **kwargs):
        self.prepare_cfg(kwargs)
        super().__init__(*args, **kwargs)
        self.tracker_cfg = tracker
        self.freeze_detector = freeze_detector
        if self.freeze_detector:
            self._freeze_detector()

    def _freeze_detector(self):
        self.backbone.eval()
        self.neck.eval()
        self.rpn_head.eval()
        self.roi_head.bbox_head.eval()
        for param in self.backbone.parameters():
            param.requires_grad = False
        for param in self.neck.parameters():
            param.requires_grad = False
        for param in self.rpn_head.parameters():
            param.requires_grad = False
        for param in self.roi_head.bbox_head.parameters():
            param.requires_grad = False

    def prepare_cfg(self, kwargs):
        if kwargs.get('train_cfg', False):
            kwargs['roi_head']['track_train_cfg'] = kwargs['train_cfg'].get(
                'embed', None)

    def init_tracker(self):
        self.tracker = build_tracker(self.tracker_cfg)

    def forward_train(self,
                      img,
                      img_metas,
                      gt_bboxes,
                      gt_labels,
                      gt_match_indices,
                      ref_img,
                      ref_img_metas,
                      ref_gt_bboxes,
                      ref_gt_labels,
                      ref_gt_match_indices,
                      gt_bboxes_ignore=None,
                      gt_masks=None,
                      ref_gt_bboxes_ignore=None,
                      ref_gt_masks=None,
                      **kwargs):
        x = self.extract_feat(img)

        losses = dict()

        # RPN forward and loss
        proposal_cfg = self.train_cfg.get('rpn_proposal', self.test_cfg.rpn)
        rpn_losses, proposal_list = self.rpn_head.forward_train(
            x,
            img_metas,
            gt_bboxes,
            gt_labels=None,
            gt_bboxes_ignore=gt_bboxes_ignore,
            proposal_cfg=proposal_cfg)
        losses.update(rpn_losses)

        ref_x = self.extract_feat(ref_img)
        ref_proposals = self.rpn_head.simple_test_rpn(ref_x, ref_img_metas)

        roi_losses = self.roi_head.forward_train(
            x, img_metas, proposal_list, gt_bboxes, gt_labels,
            gt_match_indices, ref_x, ref_img_metas, ref_proposals,
            ref_gt_bboxes, ref_gt_labels, gt_bboxes_ignore, gt_masks,
            ref_gt_bboxes_ignore, **kwargs)
        losses.update(roi_losses)

        return losses

    def simple_test(self, img, img_metas, detection_only=False,rescale=False):
        # TODO inherit from a base tracker
        assert self.roi_head.with_track, 'Track head must be implemented.'
        frame_id = img_metas[0].get('frame_id', -1)
        if frame_id == 0:
            self.init_tracker()

        x = self.extract_feat(img)
        proposal_list = self.rpn_head.simple_test_rpn(x, img_metas)
        det_bboxes, det_labels, track_feats = self.roi_head.simple_test(
            x, img_metas, proposal_list, rescale)

        if track_feats is not None and not detection_only:
            bboxes, labels, ids = self.tracker.match(
                bboxes=det_bboxes,
                labels=det_labels,
                embeds=track_feats,
                frame_id=frame_id)

        bbox_result = bbox2result(det_bboxes, det_labels,
                                  self.roi_head.bbox_head.num_classes)

        if track_feats is not None and not detection_only:
            track_result = track2result(bboxes, labels, ids,
                                        self.roi_head.bbox_head.num_classes)
        else:
            from collections import defaultdict
            track_result = defaultdict(list)
        return dict(bbox_results=bbox_result, track_results=track_result)
