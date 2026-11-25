package com.marda.gateway;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "gateway.canary")
public class GatewayCanaryProperties {

    private boolean enabled = true;

    /** 命中灰度的用户百分比；0 = 全稳定版，100 = 全灰度。 */
    private int percent = 0;

    /** 灰度实例地址；为空时即使 percent > 0 也全部走稳定版。 */
    private String canaryUri = "";

    /** 是否允许 X-Canary 头强制覆盖；默认关闭，避免客户端自己给自己灰度。 */
    private boolean headerEnabled = false;

    private String headerName = "X-Canary";

    /** 只对这条路由做灰度。 */
    private String routeId = "learning";

    /** 分桶盐，必须跨实例一致。 */
    private String salt = "marda-gateway";

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public int getPercent() {
        return percent;
    }

    public void setPercent(int percent) {
        this.percent = percent;
    }

    public String getCanaryUri() {
        return canaryUri;
    }

    public void setCanaryUri(String canaryUri) {
        this.canaryUri = canaryUri;
    }

    public boolean isHeaderEnabled() {
        return headerEnabled;
    }

    public void setHeaderEnabled(boolean headerEnabled) {
        this.headerEnabled = headerEnabled;
    }

    public String getHeaderName() {
        return headerName;
    }

    public void setHeaderName(String headerName) {
        this.headerName = headerName;
    }

    public String getRouteId() {
        return routeId;
    }

    public void setRouteId(String routeId) {
        this.routeId = routeId;
    }

    public String getSalt() {
        return salt;
    }

    public void setSalt(String salt) {
        this.salt = salt;
    }
}
