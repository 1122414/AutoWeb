import sys
import os
import json

# 将项目根目录添加到 sys.path
sys.path.append(os.path.dirname(__file__))
from skills.dom_compressor import DOMCompressor

str1 = r"""{
  "t": "body",
  "x": "/html/body",
  "kids": [
    {
      "t": "div",
      "x": "/html/body/div[1]",
      "c": "homepage",
      "kids": [
        {
          "t": "div",
          "x": "/html/body/div[1]/div[1]",
          "c": "header",
          "kids": [
            {
              "t": "div",
              "x": "/html/body/div[1]/div[1]/div[1]",
              "c": "header-box",
              "kids": [
                {
                  "t": "div",
                  "x": "/html/body/div[1]/div[1]/div[1]/div[1]",
                  "c": "logo ",
                  "kids": [
                    {
                      "t": "a",
                      "x": "/html/body/div[1]/div[1]/div[1]/div[1]/a[1]",
                      "c": "logosg",
                      "href": "/",
                      "title": "网飞啦",
                      "kids": [
                        {
                          "t": "img",
                          "x": "/html/body/div[1]/div[1]/div[1]/div[1]/a[1]/img[2]",
                          "c": "logo1",
                          "src": "/mxtheme/images/logo.png"
                        }
                      ]
                    }
                  ]
                },
                {
                  "t": "div",
                  "x": "/html/body/div[1]/div[1]/div[1]/div[2]",
                  "c": "search-box ",
                  "kids": [
                    {
                      "t": "div",
                      "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]",
                      "c": "searchbar-main",
                      "kids": [
                        {
                          "t": "form",
                          "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]",
                          "name": "search",
                          "kids": [
                            {
                              "t": "div",
                              "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]/div[1]",
                              "c": "searchbar",
                              "kids": [
                                {
                                  "t": "input",
                                  "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]/div[1]/input[1]",
                                  "c": "search-input",
                                  "placeholder": "输入剧名、人名都可以",
                                  "type": "text",
                                  "name": "wd"
                                },
                                {
                                  "t": "a",
                                  "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]/div[1]/a[1]",
                                  "href": "../label-hot.html",
                                  "title": "热搜",
                                  "kids": [
                                    {
                                      "t": "i",
                                      "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]/div[1]/a[1]/i[1]",
                                      "c": "icon icon-ranking-o phb"
                                    },
                                    {
                                      "t": "span",
                                      "x": "/html/body/div[1]/div[1]/div[1]/div[2]/div[1]/form[1]/div[1]/a[1]/span[1]",
                                      "txt": "热搜"
                                    }
                                  ]
                                },
                                {
                                  "t": "button",
                                  "x": "//*[@id=\"searchbutton\"]",
                                  "id": "searchbutton",
                                  "c": "search-btn search-go",
                                  "type": "submit",
                                  "kids": [
                                    {
                                      "t": "i",
                                      "x": "//*[@id=\"searchbutton\"]/i[1]",
                                      "c": "icon-search"
                                    }
                                  ]
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    }
                  ]
                },
                {
                  "t": "div",
                  "x": "/html/body/div[1]/div[1]/div[1]/div[3]",
                  "c": "header-op",
                  "kids": [
                    {
                      "t": "div",
                      "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]",
                      "c": "header-op-list",
                      "kids": [
                        {
                          "t": "div",
                          "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]",
                          "c": "drop",
                          "kids": [
                            {
                              "t": "div",
                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[1]",
                              "c": "header-op-list-btn header-op-history",
                              "kids": [
                                {
                                  "t": "i",
                                  "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[1]/i[1]",
                                  "c": "icon icon-history-o"
                                },
                                {
                                  "t": "span",
                                  "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[1]/span[1]",
                                  "txt": "观影记录"
                                }
                              ]
                            },
                            {
                              "t": "div",
                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]",
                              "c": "drop-content drop-history",
                              "kids": [
                                {
                                  "t": "div",
                                  "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]",
                                  "c": "drop-content-box",
                                  "kids": [
                                    {
                                      "t": "ul",
                                      "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]",
                                      "c": "drop-content-items historical",
                                      "kids": [
                                        {
                                          "t": "li",
                                          "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]/li[1]",
                                          "c": "drop-item drop-item-title",
                                          "kids": [
                                            {
                                              "t": "i",
                                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]/li[1]/i[1]",
                                              "c": "icon icon-history"
                                            },
                                            {
                                              "t": "strong",
                                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]/li[1]/strong[1]",
                                              "txt": "我的观影记录"
                                            }
                                          ]
                                        },
                                        {
                                          "t": "li",
                                          "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]/li[2]",
                                          "c": "drop-item drop-item-content nolist",
                                          "kids": [
                                            {
                                              "t": "div",
                                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[2]/div[1]/ul[1]/li[2]/div[1]",
                                              "c": "drop-prompt",
                                              "txt": "暂无观看影片的记录"
                                            }
                                          ]
                                        }
                                      ]
                                    }
                                  ]
                                }
                              ]
                            },
                            {
                              "t": "div",
                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[1]/div[3]",
                              "c": "shortcuts-mobile-overlay"
                            }
                          ]
                        },
                        {
                          "t": "div",
                          "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[2]",
                          "c": "member_group",
                          "kids": [
                            {
                              "t": "img",
                              "x": "/html/body/div[1]/div[1]/div[1]/div[3]/div[1]/div[2]/img[1]",
                              "c": "useimg",
                              "src": "./static/images/touxiang.png"
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "t": "div",
          "x": "/html/body/div[1]/div[2]",
          "c": "sidebar",
          "kids": [
            {
              "t": "div",
              "x": "/html/body/div[1]/div[2]/div[1]",
              "c": "navbar swiper-container-initialized swiper-container-horizontal swiper-container-pointer-events swiper-container-free-mode",
              "kids": [
                {
                  "t": "ul",
                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]",
                  "id": "swiper-wrapper-c6da7e3b49108104d8",
                  "c": "navbar-items swiper-wrapper",
                  "kids": [
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]",
                      "c": "swiper-slide navbar-item active swiper-slide-active",
                      "aria-label": "1 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]/a[1]",
                          "c": "links",
                          "href": "/",
                          "kids": [
                            {
                              "t": "div",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]/a[1]/div[1]",
                              "c": "current"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]/a[1]/i[2]",
                              "c": "icon icon-home-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[1]/a[1]/strong[1]/span[1]",
                                  "txt": "首页"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[2]",
                      "c": "navbar-hr"
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]",
                      "c": "swiper-slide navbar-item swiper-slide-next",
                      "aria-label": "2 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]/a[1]",
                          "c": "links",
                          "href": "/vod-show-id-1.html",
                          "title": "剧集",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]/a[1]/i[2]",
                              "c": "icon-tv-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[3]/a[1]/strong[1]/span[1]",
                                  "txt": "剧集"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "3 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]/a[1]",
                          "c": "links",
                          "href": "/vod-show-id-2.html",
                          "title": "动漫",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]/a[1]/i[2]",
                              "c": "icon-dm-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[4]/a[1]/strong[1]/span[1]",
                                  "txt": "动漫"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "4 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]/a[1]",
                          "c": "links",
                          "href": "/vod-show-id-3.html",
                          "title": "电影",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]/a[1]/i[2]",
                              "c": "icon-dy-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[5]/a[1]/strong[1]/span[1]",
                                  "txt": "电影"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "5 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]/a[1]",
                          "c": "links",
                          "href": "/vod-show-id-4.html",
                          "title": "综艺",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]/a[1]/i[2]",
                              "c": "icon-zy-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[6]/a[1]/strong[1]/span[1]",
                                  "txt": "综艺"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "6 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]/a[1]",
                          "c": "links",
                          "href": "/vod-show-id-25.html",
                          "title": "短剧",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]/a[1]/i[2]",
                              "c": "icon-bilibili"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[7]/a[1]/strong[1]/span[1]",
                                  "txt": "短剧"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[8]",
                      "c": "navbar-hr"
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "7 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]",
                          "c": "links",
                          "href": "/label-new.html",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]/i[2]",
                              "c": "icon-update-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]/strong[1]/span[1]",
                                  "txt": "新剧"
                                }
                              ]
                            },
                            {
                              "t": "small",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[9]/a[1]/small[1]",
                              "txt": "229"
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "t": "li",
                      "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]",
                      "c": "swiper-slide navbar-item",
                      "aria-label": "8 / 14",
                      "role": "group",
                      "kids": [
                        {
                          "t": "a",
                          "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]/a[1]",
                          "c": "links",
                          "href": "/label-hot.html",
                          "kids": [
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]/a[1]/i[1]",
                              "c": "icon-arrow-go"
                            },
                            {
                              "t": "i",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]/a[1]/i[2]",
                              "c": "icon icon-ranking-o"
                            },
                            {
                              "t": "strong",
                              "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]/a[1]/strong[1]",
                              "kids": [
                                {
                                  "t": "span",
                                  "x": "//*[@id=\"swiper-wrapper-c6da7e3b49108104d8\"]/li[10]/a[1]/strong[1]/span[1]",
                                  "txt": "好剧"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}"""

dom_compressor = DOMCompressor(mode="lite")

result = dom_compressor.compress(str1)
result_str = json.dumps(result, ensure_ascii=False)

print(f'原len：{len(str1)}，\n压缩过后len：{len(result_str)}')


