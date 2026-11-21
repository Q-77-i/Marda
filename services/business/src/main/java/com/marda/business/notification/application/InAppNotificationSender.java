package com.marda.business.notification.application;

import com.marda.business.notification.domain.Notification;
import org.springframework.stereotype.Component;

/** in_app 送达：通知行的可见性就是送达；这里不做阻塞 I/O。 */
@Component
public class InAppNotificationSender implements NotificationSender {

    @Override
    public void send(Notification notification) {
        // no-op：读接口按 status=sent 过滤，worker 标记 sent 即对用户可见。
    }
}
