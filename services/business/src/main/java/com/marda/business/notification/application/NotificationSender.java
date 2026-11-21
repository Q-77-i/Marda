package com.marda.business.notification.application;

import com.marda.business.notification.domain.Notification;

/**
 * 通知发送端口。
 *
 * <p>MVP 只有 {@code in_app}：行可见即送达；保留接口是为了后续 email / webhook
 * 可以在**不重写 worker 重试 / DLQ 逻辑**的前提下接入。
 */
public interface NotificationSender {

    void send(Notification notification);
}
